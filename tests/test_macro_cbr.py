"""M3 contracts use recorded CBR HTML, never the network in CI."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock,patch
import threading
import unittest
from vault_fixtures import ResearchCase
from wealth_manager.adapters.macroeconomics.cbr import Provider,Transport,NoRedirect
from wealth_manager.adapters.macroeconomics.session import Session
from wealth_manager.modules.macroeconomics.refresh import Refresh,RefreshError
from wealth_manager.factory import create_app

FIX=Path(__file__).parent/'fixtures/cbr'
START=date(2026,11,1);END=date(2026,11,20)

def provider(name='rate',raw=None):return Provider(Mock(get=Mock(return_value=raw if raw is not None else (FIX/(name+'.html')).read_bytes())))

class ParserTests(unittest.TestCase):
    def test_live_fixtures_exact_values_tenors_and_unknown_publications(self):
        rate=provider().fetch('key_rate',START,END)
        self.assertEqual(len(rate),1);self.assertEqual(len(rate[0][4]),14)
        self.assertEqual(rate[0][4][0].value,Decimal('14.00'))
        curve=provider('curve').fetch('ofz_curve',START,END)
        self.assertEqual([x[2].indicator for x in curve],['ofz_zero_1y','ofz_zero_2y','ofz_zero_5y','ofz_zero_10y'])
        self.assertEqual([x[4][0].value for x in curve],list(map(Decimal,['14.18','15.01','16.44','17.05'])))
        for bundle in rate+curve:
            for point in bundle[4]:self.assertEqual(point.published_at,'');self.assertEqual(point.publication_precision,'unknown')
            self.assertEqual(bundle[3].parser_version,'cbr-html-v1')

    def test_bad_table_or_ignored_range_never_silently_imported(self):
        raw=(FIX/'rate.html').read_text()
        cases=[b'<html>Captcha</html>',b'\xff',b'',b'x'*2000001,
               raw.replace('<th>Ставка</th>','<th>Other</th>').encode(),
               raw.replace('value="01.11.2026"','value="01.10.2026"').encode(),
               raw.replace('<td>14,00</td>','<td>NaN</td>',1).encode(),
               raw.replace('<td>14,00</td>','<td>-1</td>',1).encode(),
               raw.replace('<td>19.11.2026</td>','<td>20.11.2026</td>').encode(),
               raw.replace('<td>19.11.2026</td>','<td>21.11.2026</td>').encode(),
               raw.replace('<td>14,00</td>','',1).encode(),
               raw.replace('</table>','').encode()]
        for body in cases:
            with self.subTest(size=len(body)):
                with self.assertRaises(RefreshError):provider(raw=body).fetch('key_rate',START,END)

    def test_missing_yield_is_missing_not_zero_and_header_mapping(self):
        raw=(FIX/'curve.html').read_text().replace('<td>14,18</td>','<td>—</td>',1)
        bundles=provider(raw=raw.encode()).fetch('ofz_curve',START,END)
        self.assertEqual(len(bundles[0][4]),13);self.assertEqual(len(bundles[1][4]),14)
        self.assertEqual(bundles[1][4][0].value,Decimal('15.01'))
        for bad in (raw.replace('<th>2</th>','<th>1</th>'),raw.replace('<th>10</th>','<th>11</th>')):
            with self.assertRaises(RefreshError):provider(raw=bad.encode()).fetch('ofz_curve',START,END)

    def test_transport_limits_and_failure_messages(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))
        response=Mock(status=200);response.headers.get_content_type.return_value='text/html';response.read.return_value=b'ok'
        manager=Mock();manager.__enter__=Mock(return_value=response);manager.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=manager
        with patch('urllib.request.build_opener',return_value=opener):
            self.assertEqual(Transport().get('https://www.cbr.ru/'),'ok'.encode())
            self.assertEqual(opener.open.call_args.kwargs['timeout'],15)
            response.read.assert_called_with(2000001)
            for body in (b'',b'x'*2000001):
                response.read.return_value=body
                with self.assertRaises(RefreshError):Transport().get('https://www.cbr.ru/')
            response.status=503
            with self.assertRaises(RefreshError):Transport().get('https://www.cbr.ru/')
            opener.open.side_effect=TimeoutError('private details')
            with self.assertRaises(RefreshError) as caught:Transport().get('https://www.cbr.ru/')
            self.assertNotIn('private details',str(caught.exception))

class RefreshTests(ResearchCase):
    def refresh(self,p=None):return Refresh(p or provider(),Session(self.vault),today=lambda:date(2026,11,21))
    def run_refresh(self,r=None,kind='key_rate'):return (r or self.refresh()).execute(kind,START.isoformat(),END.isoformat(),self.vault.token)

    def test_atomic_ingestion_overlap_idempotence_and_manual_preservation(self):
        self.s['macroeconomics'].save(self.observation)
        self.assertEqual(self.run_refresh(),14)
        before=self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0]
        changed=(FIX/'rate.html').read_bytes()+b'<!-- new page chrome -->'
        self.run_refresh(self.refresh(provider(raw=changed)))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0],before)
        self.assertEqual(len(self.s['macro_reports'].overview()['observations']),1)
        self.assertEqual(self.run_refresh(self.refresh(provider('curve')),'ofz_curve'),56)
        rows=self.s['macro_data_reports'].overview('2026-11-20')['rows']
        self.assertEqual(rows,[]) # retrieval is later; value dates are not publication dates
        self.vault.lock();self.vault.unlock('password123')
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0],71)

    def test_revised_value_creates_version(self):
        self.run_refresh()
        raw=(FIX/'rate.html').read_bytes().replace(b'<td>14,00</td>',b'<td>14,50</td>',1)
        self.run_refresh(self.refresh(provider(raw=raw)))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0],15)

    def test_bad_input_does_not_request_and_empty_result_does_not_write(self):
        p=Mock();r=self.refresh(p)
        for kind,start,end in [('wrong','2026-11-01','2026-11-20'),('key_rate','2013-09-16','2013-09-17'),('ofz_curve','2003-01-03','2003-01-05'),('key_rate','2025-01-01','2026-11-20'),('key_rate','2026-11-20','2026-11-01'),('key_rate','2026-11-01','2026-11-22')]:
            with self.assertRaises(RefreshError):r.execute(kind,start,end,self.vault.token)
        p.fetch.assert_not_called();before=self.dump();p.fetch.return_value=[]
        with self.assertRaises(RefreshError):self.run_refresh(r)
        self.assertEqual(self.dump(),before)

    def test_disk_failure_rolls_back_all_curve_tenors(self):
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        with patch.object(self.vault,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.run_refresh(self.refresh(provider('curve')),'ofz_curve')
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)

    def test_relock_during_fetch_discards_response_even_after_unlock(self):
        p=provider();original=p.fetch
        def fetch(*args):
            result=original(*args);self.vault.lock();self.vault.unlock('password123');return result
        p.fetch=fetch
        with self.assertRaises(RefreshError):self.run_refresh(self.refresh(p))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0],0)

    def test_http_does_not_hold_vault_lock_and_is_localized_read_only_on_get(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as s:s['vault']=self.vault.token
        # create_app builds its own service dictionary.
        runtime=app.extensions['wealth_manager'] if 'wealth_manager' in app.extensions else app.extensions['wealth_runtime']
        p=provider();original=p.fetch;acquired=[]
        def fetch(*args):
            def probe():
                ok=self.vault.lock_.acquire(timeout=.5);acquired.append(ok)
                if ok:self.vault.lock_.release()
            thread=threading.Thread(target=probe);thread.start();thread.join();return original(*args)
        p.fetch=fetch;runtime.services['macro_refresh']=self.refresh(p)
        self.s['change_language'].execute('en');before=self.dump()
        self.assertIn('Load CBR data',client.get('/macroeconomics').get_data(as_text=True));self.assertEqual(self.dump(),before)
        self.assertEqual(client.get('/macroeconomics/refresh').status_code,405)
        response=client.post('/macroeconomics/refresh',data=dict(kind='key_rate',start='2026-11-01',end='2026-11-20'))
        self.assertEqual(response.status_code,302);self.assertEqual(acquired,[True])
        p.http.get.side_effect=RefreshError('Не удалось получить данные ЦБ. Сохранённые данные не изменены')
        before=self.dump();response=client.post('/macroeconomics/refresh',data=dict(kind='key_rate',start='2026-11-01',end='2026-11-20'))
        self.assertEqual(response.status_code,400);self.assertIn('Could not fetch CBR data',response.get_data(as_text=True));self.assertEqual(self.dump(),before)

    def test_locked_request_redirects_and_session_change_cannot_render_new_vault(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client();runtime=app.extensions['wealth_manager']
        with client.session_transaction() as s:s['vault']=self.vault.token
        p=provider();original=p.fetch
        def relock(*args):
            result=original(*args);self.vault.lock();self.vault.unlock('password123');return result
        p.fetch=relock;runtime.services['macro_refresh']=self.refresh(p)
        form=dict(kind='key_rate',start='2026-11-01',end='2026-11-20')
        response=client.post('/macroeconomics/refresh',data=form)
        self.assertEqual(response.status_code,302);self.assertIn('/unlock',response.location)
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0],0)
        self.vault.lock();p.http.get.reset_mock()
        self.assertEqual(client.post('/macroeconomics/refresh',data=form).status_code,302);p.http.get.assert_not_called()

    def test_invalid_later_bundle_cannot_partially_import(self):
        from dataclasses import replace
        bundles=provider('curve').fetch('ofz_curve',START,END)
        broken=list(bundles[-1]);broken[-1]=[replace(broken[-1][0],period_end='2026-12-01')]
        bundles[-1]=tuple(broken);p=Mock();p.fetch.return_value=bundles;before=self.dump()
        with self.assertRaises(ValueError):self.run_refresh(self.refresh(p),'ofz_curve')
        self.assertEqual(self.dump(),before)

    def test_return_to_earlier_value_with_new_evidence_keeps_revision(self):
        self.run_refresh()
        raw=(FIX/'rate.html').read_bytes()
        self.run_refresh(self.refresh(provider(raw=raw.replace(b'<td>14,00</td>',b'<td>14,50</td>',1))))
        self.run_refresh(self.refresh(provider(raw=raw+b'<!-- later correction -->')))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0],16)
