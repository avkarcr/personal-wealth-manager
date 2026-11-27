"""M5: recorded ISS contracts, durable checkpoints and vault lifecycle."""
import json
import threading
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from vault_fixtures import ResearchCase
from wealth_manager.adapters.macroeconomics.moex import Provider, Transport, MARKETS
from wealth_manager.adapters.macroeconomics.providers import Providers
from wealth_manager.adapters.macroeconomics.session import Session
from wealth_manager.modules.macroeconomics.scheduling import Scheduler
from wealth_manager.modules.macroeconomics.refresh import RefreshError
from wealth_manager.factory import create_app
from wealth_manager.adapters.runtime import ApplicationRuntime

FIX=Path(__file__).parent/'fixtures/moex'
START='2024-01-09';END='2024-01-12'

def provider(kind='IMOEX',raw=None):
    return Provider(Mock(get=Mock(return_value=raw if raw is not None else (FIX/(kind+'.json')).read_bytes())))

def bundles():return provider().fetch('IMOEX',date.fromisoformat(START),date.fromisoformat(END))

def payload():return json.loads((FIX/'IMOEX.json').read_text())

def encoded(data):return json.dumps(data).encode()

class MarketTests(unittest.TestCase):
    def test_recorded_contracts_separate_price_total_return_and_rates(self):
        for kind,expected in zip(MARKETS,['3155.55','7152.12','121.02','617.8','15.84']):
            b=provider(kind).fetch(kind,date.fromisoformat(START),date.fromisoformat(END))[0]
            self.assertEqual(b[2].indicator,'moex_'+kind.lower())
            self.assertEqual(b[2].unit,'percent' if kind=='RUSFAR' else 'index')
            self.assertEqual(b[4][0].value,Decimal(expected));self.assertEqual(len(b[4]),4)
            for point in b[4]:
                point.validate(b[2]);self.assertEqual(point.published_at,'');self.assertEqual(point.publication_precision,'unknown')
            for record in b[:4]:record.validate()

    def test_pagination_complete_empty_and_non_selected_sessions(self):
        data=payload();rows=data['history']['data'];data['history']['data']=rows[:2];data['history.cursor']['data']=[[0,4,2]]
        other=payload();other['history']['data']=rows[2:];other['history.cursor']['data']=[[2,4,2]]
        p=Provider(Mock(get=Mock(side_effect=[encoded(data),encoded(other)])))
        self.assertEqual(sum(len(b[4]) for b in p.fetch('IMOEX',date(2024,1,9),date(2024,1,12))),4)
        self.assertIn('start=2',p.http.get.call_args.args[0])
        data['history']['data']=[];data['history.cursor']['data']=[[0,0,100]]
        self.assertEqual(provider(raw=encoded(data)).fetch('IMOEX',date(2024,1,9),date(2024,1,12)),[])
        data=payload();cols=data['history']['columns']
        for row in data['history']['data']:row[cols.index('TRADINGSESSION')]='1'
        self.assertEqual(provider(raw=encoded(data)).fetch('IMOEX',date(2024,1,9),date(2024,1,12)),[])
        data=payload();data['history']['data'][0][cols.index('CLOSE')]=None
        self.assertEqual(len(provider(raw=encoded(data)).fetch('IMOEX',date(2024,1,9),date(2024,1,12))[0][4]),3)

    def test_reject_bad_history_identity_values_cursor_and_partial_pages(self):
        cases=[b'captcha',b'{}']
        for key,value in [('CLOSE','oops'),('CLOSE','NaN'),('CLOSE',0),('SECID','OTHER'),('TRADEDATE','2024-02-01')]:
            data=payload();data['history']['data'][0][data['history']['columns'].index(key)]=value;cases.append(encoded(data))
        data=payload();data['history']['data'][1]=data['history']['data'][0];cases.append(encoded(data))
        data=payload();data['history']['data'].pop();cases.append(encoded(data))
        data=payload();data['history']['columns'][0]=data['history']['columns'][1];cases.append(encoded(data))
        for cursor in ([],[[1,4,100]],[[0,401,100]],[[0,4,0]],[[0,4,101]],[[0,4.5,100]]):
            data=payload();data['history.cursor']['data']=cursor;cases.append(encoded(data))
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(RefreshError):provider(raw=raw).fetch('IMOEX',date(2024,1,9),date(2024,1,12))
        data=payload();data['history']['data']=data['history']['data'][:2];data['history.cursor']['data']=[[0,4,2]]
        other=payload();other['history.cursor']['data']=[[2,5,2]]
        with self.assertRaises(RefreshError):Provider(Mock(get=Mock(side_effect=[encoded(data),encoded(other)]))).fetch('IMOEX',date(2024,1,9),date(2024,1,12))
        pages=[]
        for i in range(4):
            d=payload();d['history']['data']=d['history']['data'][i:i+1];d['history.cursor']['data']=[[i,5,1]];pages.append(encoded(d))
        with self.assertRaises(RefreshError):Provider(Mock(get=Mock(side_effect=pages))).fetch('IMOEX',date(2024,1,9),date(2024,1,12))

    def test_bounds_cancel_and_transport(self):
        p=provider()
        for kind,start,end in [('bad',date(2024,1,9),date(2024,1,12)),('IMOEX',date(2024,1,1),date(2024,5,1))]:
            with self.assertRaises(RefreshError):p.fetch(kind,start,end)
        with self.assertRaises(RefreshError):p.fetch('IMOEX',date(2024,1,9),date(2024,1,12),lambda:True)
        p.http.get.assert_not_called()
        response=Mock(status=200);response.read.return_value=b'{}';context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=context
        with patch('urllib.request.build_opener',return_value=opener):
            self.assertEqual(Transport().get('https://iss.moex.com/'),b'{}')
            self.assertEqual(opener.open.call_args.kwargs['timeout'],15)
            for raw in (b'',b'x'*2000001):
                response.read.return_value=raw
                with self.assertRaises(RefreshError):Transport().get('https://iss.moex.com/')
            response.status=503
            with self.assertRaises(RefreshError):Transport().get('https://iss.moex.com/')
        p=Providers();p.cbr=Mock();p.moex=Mock();p.fetch('key_rate',1,2);p.cbr.fetch.assert_called_once_with('key_rate',1,2)
        p.fetch('IMOEX',1,2);p.moex.fetch.assert_called_once()

class ScheduleTests(ResearchCase):
    def scheduler(self):
        self.clock=Mock(return_value='2024-01-13T12:00:00+00:00')
        self.provider=Mock(fetch=Mock(return_value=bundles()))
        return Scheduler(self.provider,Session(self.vault),self.clock)
    def configure(self,s,kind='IMOEX',enabled=True,start=START,hours=24,access='familiarization'):
        s.configure(kind,enabled,start,hours,access,self.vault.token)
    def run_refresh(self,s,**kwargs):return s.run('IMOEX',START,END,self.vault.token,**kwargs)
    def count(self):return self.vault.con.execute('SELECT count(*) FROM macro_revisions').fetchone()[0]
    def state(self,s):return s.state(self.vault.token)

    def test_disabled_defaults_access_validation_and_no_get_effects(self):
        s=self.scheduler();before=self.dump();s.tick();self.assertEqual(self.dump(),before);self.provider.fetch.assert_not_called()
        with self.assertRaises(RefreshError):self.run_refresh(s)
        for kw in [dict(kind='bad'),dict(hours=0),dict(hours=169),dict(start='2024-02-01'),dict(access='bad'),dict(access='disabled')]:
            with self.subTest(kw=kw):
                with self.assertRaises(RefreshError):self.configure(s,**kw)
        self.configure(s,enabled=False);s.tick();self.provider.fetch.assert_not_called()
        self.assertEqual(self.run_refresh(s,scheduled=True),0)
        self.configure(s);self.configure(s,kind='key_rate',enabled=False,access='disabled');s.tick();self.provider.fetch.assert_not_called()
        for kind,start,end in [('bad',START,END),('IMOEX',END,START),('IMOEX','2023-01-01',END),('key_rate','2013-09-01',END),('IMOEX',START,'2024-01-14')]:
            with self.assertRaises(RefreshError):s.run(kind,start,end,self.vault.token)

    def test_checkpoint_persisted_with_data_overlap_and_manual_not_advance(self):
        s=self.scheduler();self.configure(s);self.assertEqual(self.run_refresh(s),4)
        self.assertEqual(self.state(s)['schedules']['IMOEX']['checkpoint'],'')
        s.tick();state=self.state(s);self.assertEqual(state['schedules']['IMOEX']['checkpoint'],END);self.assertEqual(self.count(),4)
        self.assertEqual(state['runs'][-1]['status'],'success')
        self.provider.fetch.reset_mock();s.tick();self.provider.fetch.assert_not_called()
        self.clock.return_value='2024-01-14T12:00:00+00:00';s.tick()
        self.assertEqual(self.provider.fetch.call_args.args[1:3],(date(2024,1,9),date(2024,1,13)))
        self.vault.lock();self.vault.unlock('password123');self.assertEqual(self.state(s)['schedules']['IMOEX']['checkpoint'],'2024-01-13');self.assertEqual(self.count(),4)
        self.configure(s,start='2024-01-10');self.assertEqual(self.state(s)['schedules']['IMOEX']['checkpoint'],'')

    def test_catchup_empty_holidays_overlap_and_retry_backoff(self):
        s=self.scheduler();self.provider.fetch.return_value=[];self.configure(s,start='2023-01-01');s.tick()
        cfg=self.state(s)['schedules']['IMOEX'];self.assertEqual(cfg['checkpoint'],'2023-03-31');self.assertEqual(cfg['next_due'],'2024-01-13T12:01:00.000000+00:00')
        self.assertEqual(self.state(s)['runs'][-1]['status'],'empty');self.assertEqual(self.count(),0)
        self.clock.return_value='2024-01-13T12:01:00+00:00';s.tick();self.assertEqual(self.provider.fetch.call_args.args[1],date(2023,3,25))
        checkpoint=self.state(s)['schedules']['IMOEX']['checkpoint'];self.clock.return_value='2024-01-13T12:02:00+00:00'
        self.provider.fetch.side_effect=TimeoutError('private source error')
        with self.assertRaises(TimeoutError):s.tick()
        state=self.state(s);cfg=state['schedules']['IMOEX'];self.assertEqual(cfg['checkpoint'],checkpoint);self.assertEqual(cfg['next_due'],'2024-01-13T12:07:00.000000+00:00');self.assertEqual(cfg['failures'],1)
        self.assertNotIn('private source error',json.dumps(state));self.assertEqual(state['runs'][-1]['status'],'failed')
        self.provider.fetch.reset_mock();s.tick();self.provider.fetch.assert_not_called()
        self.clock.return_value='2024-01-13T12:07:00+00:00'
        with self.assertRaises(TimeoutError):s.tick()
        self.assertEqual(self.state(s)['schedules']['IMOEX']['next_due'],'2024-01-13T12:17:00.000000+00:00')

    def test_session_lock_reopen_and_shutdown_cancel_discard_inflight(self):
        s=self.scheduler();self.configure(s);oldtoken=self.vault.token
        def fetch(*args):self.vault.lock();self.vault.unlock('password123');return bundles()
        self.provider.fetch.side_effect=fetch
        with self.assertRaises(RefreshError):self.run_refresh(s,scheduled=True)
        self.assertNotEqual(self.vault.token,oldtoken);self.assertEqual(self.count(),0);self.assertEqual(self.state(s)['runs'][-1]['status'],'running')
        self.provider.fetch.side_effect=None;self.run_refresh(s);self.assertEqual(self.state(s)['runs'][-2]['status'],'interrupted')
        self.assertEqual(self.count(),4)
        cancelled=Mock(side_effect=[False,True])
        with self.assertRaises(RefreshError):self.run_refresh(s,scheduled=True,cancel=cancelled)
        self.assertEqual(self.state(s)['schedules']['IMOEX']['checkpoint'],'');self.assertEqual(self.state(s)['runs'][-1]['status'],'failed')
        with self.assertRaises(RefreshError):self.run_refresh(s,cancel=lambda:True)
        self.vault.lock();self.provider.fetch.reset_mock();s.tick();self.provider.fetch.assert_not_called()

    def test_configuration_change_and_concurrent_run_discard_response(self):
        s=self.scheduler();self.configure(s)
        def fetch(*args):
            with self.assertRaises(RefreshError):self.run_refresh(s)
            s.tick() # busy gate, no recursive fetch
            self.configure(s,enabled=False)
            return bundles()
        self.provider.fetch.side_effect=fetch
        with self.assertRaises(RefreshError):self.run_refresh(s,scheduled=True)
        self.assertEqual(self.count(),0);self.assertFalse(self.state(s)['schedules']['IMOEX']['enabled']);self.assertEqual(self.state(s)['schedules']['IMOEX']['failures'],0)
        self.provider.fetch.side_effect=lambda *args:(self.configure(s,kind='key_rate',enabled=False,access='disabled') or bundles())
        with self.assertRaises(RefreshError):self.run_refresh(s)
        self.assertEqual(self.count(),0)

    def test_atomic_data_checkpoint_rollback_on_disk_failure(self):
        s=self.scheduler();self.configure(s);save=self.vault.save;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(True)
            if len(calls)==2:raise OSError('full')
            return save(*args,**kwargs)
        with patch.object(self.vault,'save',side_effect=fail_second):
            with self.assertRaises(OSError):self.run_refresh(s,scheduled=True)
        self.assertEqual(self.count(),0);self.assertEqual(self.state(s)['schedules']['IMOEX']['checkpoint'],'')
        self.vault.lock();self.vault.unlock('password123');self.assertEqual(self.count(),0);self.assertEqual(self.state(s)['runs'][-1]['status'],'failed')

    def test_bad_later_bundle_is_atomic_and_journal_bounded(self):
        s=self.scheduler();self.configure(s);b=bundles()[0];bad=list(b);bad[4]=[replace(b[4][0],period_start='2024-02-01',period_end='2024-02-01')]
        self.provider.fetch.return_value=[b,tuple(bad)]
        with self.assertRaises(RefreshError):self.run_refresh(s)
        self.assertEqual(self.count(),0)
        bad=list(b);bad[2]=replace(b[2],indicator='other');self.provider.fetch.return_value=[tuple(bad)]
        with self.assertRaises(RefreshError):self.run_refresh(s)
        self.provider.fetch.return_value=[b,b]
        with self.assertRaises(RefreshError):self.run_refresh(s)
        with s.session.write(self.vault.token) as uow:
            state=uow.macro_data.refresh_state();state['runs']=[dict(state['runs'][0],id=str(i)) for i in range(100)];uow.macro_data.save_refresh_state(state);uow.commit()
        self.provider.fetch.return_value=bundles();self.run_refresh(s);self.assertEqual(len(self.state(s)['runs']),100);self.assertEqual(self.state(s)['runs'][0]['id'],'1')

    def test_today_only_schedule_skipped_and_background_does_not_touch_idle(self):
        s=self.scheduler();self.configure(s,start='2024-01-13');s.tick();self.provider.fetch.assert_not_called()
        self.vault.last_activity=0;s.tick();self.provider.fetch.assert_not_called()

    def test_settings_localization_validation_manual_route_and_unlocked_network(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client();runtime=app.extensions['wealth_manager'];s=self.scheduler()
        runtime.services['macro_scheduler']=s;runtime.services['macro_refresh']=s
        with client.session_transaction() as session:session['vault']=self.vault.token
        self.s['change_language'].execute('en');before=self.dump();response=client.get('/settings/macroeconomics')
        self.assertEqual(response.status_code,200);self.assertIn('Refresh journal',response.get_data(as_text=True));self.assertEqual(self.dump(),before)
        form=dict(kind='IMOEX',start=START,hours='24',access='familiarization',enabled='on')
        self.assertEqual(client.post('/settings/macroeconomics',data=dict(form,hours='bad')).status_code,400)
        self.assertEqual(client.post('/settings/macroeconomics',data=form).status_code,302)
        acquired=[]
        def fetch(*args):
            def probe():
                ok=self.vault.lock_.acquire(timeout=.5);acquired.append(ok)
                if ok:self.vault.lock_.release()
            t=threading.Thread(target=probe);t.start();t.join();return bundles()
        self.provider.fetch.side_effect=fetch
        self.assertEqual(client.post('/macroeconomics/refresh',data=dict(kind='IMOEX',start=START,end=END)).status_code,302)
        self.assertEqual(acquired,[True]);self.assertEqual(self.count(),4)
        self.s['change_language'].execute('ru');self.assertIn('Журнал обновлений',client.get('/settings/macroeconomics').get_data(as_text=True))
        self.vault.lock();self.assertEqual(client.get('/settings/macroeconomics').status_code,302)

    def test_background_network_does_not_block_auto_lock_or_shutdown(self):
        s=self.scheduler();self.configure(s);entered=threading.Event();release=threading.Event();locked=threading.Event()
        def fetch(*args):entered.set();release.wait(2);return bundles()
        self.provider.fetch.side_effect=fetch;runtime=ApplicationRuntime(self.vault,{'macro_scheduler':s},poll_seconds=.01)
        original=self.vault.lock
        def lock():original();locked.set()
        with patch.object(self.vault,'lock',side_effect=lock):
            try:
                runtime.start();macro=runtime._macro_thread;runtime.start();self.assertIs(macro,runtime._macro_thread)
                self.assertTrue(entered.wait(2));self.vault.last_activity=0;self.assertTrue(locked.wait(2))
            finally:release.set();runtime.close()
        self.assertFalse(macro.is_alive());self.vault.unlock('password123');self.assertEqual(self.count(),0)

    def test_worker_error_retries_and_failed_second_thread_start_is_clean(self):
        scheduler=Mock();event=threading.Event()
        def tick(*args):event.set();raise ValueError('bad')
        scheduler.tick.side_effect=tick;runtime=ApplicationRuntime(self.vault,{'macro_scheduler':scheduler},poll_seconds=.01)
        with patch('wealth_manager.adapters.runtime.logger') as logger:
            runtime.start();self.assertTrue(event.wait(2));runtime.close();self.assertTrue(logger.warning.called)
        self.vault.unlock('password123');runtime=ApplicationRuntime(self.vault,{'macro_scheduler':scheduler},poll_seconds=.01)
        start=threading.Thread.start;threads=[]
        def launch(t):
            threads.append(t)
            if len(threads)==2:raise RuntimeError('cannot start')
            start(t)
        with patch.object(threading.Thread,'start',launch):
            with self.assertRaises(RuntimeError):runtime.start()
        self.assertFalse(threads[0].is_alive());self.assertFalse(runtime.running);runtime.close()
