"""M8: coherent snapshots, point-in-time phase history, missingness and HTTP."""
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import json
import unittest
import test_economic_cycle as fixtures
from test_macro_derived import monthly,daily,row
from wealth_manager.modules.macro_dashboard.application import Dashboard,anchors
from wealth_manager.modules.macro_dashboard.charts import history,with_breaks
from wealth_manager.modules.economic_cycle.config import defaults,validate
from wealth_manager.modules.macroeconomics.data import DataReports
from wealth_manager.adapters.macroeconomics.session import Session
from wealth_manager.factory import create_app
from wealth_manager.shared.records import RecordError
from wealth_manager.modules.macroeconomics.refresh import RefreshError


class DashboardTests(unittest.TestCase):
    setUp=fixtures.CycleTests.setUp
    dump=fixtures.CycleTests.dump
    ingest=fixtures.CycleTests.ingest
    seed=fixtures.CycleTests.seed
    service=fixtures.CycleTests.service
    def dashboard(self):return Dashboard(Session(self.vault),lambda:'2026-11-22T12:00:00+00:00')

    def test_empty_page_is_read_only_and_never_fetches_or_claims_a_phase(self):
        before=self.dump()
        with patch.object(self.s['macro_scheduler'].provider,'fetch',side_effect=AssertionError('GET must be offline')):
            r=self.dashboard().overview(self.vault.token)
        self.assertEqual(before,self.dump());self.assertTrue(r['empty']);self.assertEqual(r['result']['phase'],'INSUFFICIENT_DATA')
        self.assertTrue(all(k['value'] is None for k in r['kpis']));self.assertEqual(r['graph']['points'],[])
        self.assertIsNone(r['last_received']);self.assertEqual(len(r['refresh']),7)

    def test_range_only_changes_display_and_keeps_gdp_warmup_and_same_snapshot(self):
        cfg,_=self.seed();service=self.dashboard();p=service.overview(self.vault.token,chart='gap',window='1')
        self.assertTrue(p['graph']['points']);self.assertTrue(all('2025-11-22'<=x['date']<='2026-11-22' for x in p['graph']['points']))
        full=service.overview(self.vault.token,chart='gap',window='all')
        self.assertEqual(p['result'],full['result']);self.assertEqual(p['fingerprint'],full['fingerprint'])
        self.assertGreater(len(full['graph']['points']),len(p['graph']['points']))
        self.assertEqual(p['result'],self.service().preview(self.vault.token)['result'])
        self.assertEqual(p['kpis'][0]['date'],'2026-09-30');self.assertEqual(p['kpis'][0]['previous_date'],'2026-06-30')
        self.assertEqual(len(p['groups']),5);self.assertTrue(all(s['date'] for s in p['sources']))
        for window in ('1','3','5','10','all'):
            self.assertTrue(service.overview(self.vault.token,window=window)['graph']['points'])

    def test_phase_markers_use_separate_known_vintages_not_the_final_snapshot(self):
        self.seed();service=self.dashboard();snapshot=service.session.dashboard_snapshot(self.vault.token)
        snapshot=deepcopy(snapshot)
        for r in snapshot['rows']:
            r.update(available_at='2026-11-22T01:00:00.000000+00:00',publication_precision='unknown')
        with patch.object(service.session,'dashboard_snapshot',return_value=snapshot):
            strict=service.overview(self.vault.token,chart='phases',window='3')
            latest=service.overview(self.vault.token,chart='phases',window='3',mode='latest')
        self.assertTrue(all(p['coverage']==0 and p['phase']=='INSUFFICIENT_DATA' for p in strict['graph']['points'][:-1]))
        self.assertGreater(strict['graph']['points'][-1]['coverage'],0)
        self.assertTrue(any(p['coverage']>0 for p in latest['graph']['points'][:-1]))
        self.assertEqual(strict['graph']['points'][-1]['phase'],strict['result']['phase'])
        self.assertEqual(strict['graph']['points'][-1]['as_of'],strict['result']['as_of'])
        self.assertTrue(all(p['break_before'] for p in strict['graph']['points']))
        dates,step=anchors('1900-01-01','2026-11-22');self.assertGreater(step,1);self.assertLessEqual(len(dates),241);self.assertEqual(dates[-1],'2026-11-22')

    def test_every_chart_pagination_and_invalid_parameters(self):
        self.seed();service=self.dashboard()
        for chart in ('gdp','gap','cpi','key_rate','curve','spread','equities','bonds','money','phases'):
            r=service.overview(self.vault.token,chart=chart,window='all');self.assertTrue(r['graph']['points'],chart)
        data=daily('moex_imoex',[f'2026-{m:02d}-{d:02d}' for m in (8,9,10) for d in range(1,29)],[100+i for i in range(84)])
        self.ingest(data);p=service.overview(self.vault.token,chart='equities',window='all',page=2)
        self.assertEqual(p['page'],2);self.assertLessEqual(len(p['table']),50);self.assertGreater(p['count'],50)
        self.assertLess(p['table'][0]['date'],p['graph']['points'][-1]['date'])
        self.assertEqual(service.overview(self.vault.token,chart='equities',page=9999)['page'],p['pages'])
        for kwargs in (dict(window='2'),dict(chart='unknown'),dict(page=0),dict(page=True),dict(mode='x'),dict(as_of='2099-01-01')):
            with self.assertRaises(RecordError):service.overview(self.vault.token,**kwargs)
        token=self.vault.token;self.vault.lock()
        with self.assertRaises(RecordError):service.overview(token)

    def test_loader_failure_and_last_success_are_both_visible_without_data_loss(self):
        self.seed();state=dict(schedules={'key_rate':dict(enabled=True)},access='disabled',runs=[dict(kind='key_rate',status='success',finished='2026-11-20T00:00:00Z'),dict(kind='key_rate',status='failed',finished='2026-11-21T00:00:00Z')])
        service=self.dashboard();snap=service.session.dashboard_snapshot(self.vault.token);snap['refresh']=state
        with patch.object(service.session,'dashboard_snapshot',return_value=snap):r=service.overview(self.vault.token)
        self.assertEqual(r['refresh'][0]['last']['status'],'failed');self.assertEqual(r['refresh'][0]['last_success'],'2026-11-20T00:00:00Z')
        self.assertIsNotNone(r['kpis'][2]['value']);self.assertFalse(r['refresh'][2]['allowed'])

    def test_disabled_and_missing_group_diagnostics(self):
        cfg,_=self.seed();cfg['groups']['markets']='0';cfg['series']['yield_2y']=None;self.service().configure(self.vault.token,cfg,1)
        p=self.dashboard().overview(self.vault.token)
        self.assertEqual(p['groups'][-1]['signal'],'off');self.assertIsNone(p['kpis'][-1]['value'])
        self.assertIsNone(p['kpis'][-1]['delta']);self.assertEqual(p['kpis'][-1]['reason'],'missing_pair')

    def test_http_ru_en_links_forms_and_current_refresh_preserve_view(self):
        self.seed();app=create_app({'TESTING':True},vault=self.vault);client=app.test_client();runtime=app.extensions['wealth_manager']
        runtime.services['macro_dashboard'].clock=lambda:'2026-11-22T12:00:00+00:00'
        runtime.services['macro_scheduler'].clock=lambda:'2026-11-22T12:00:00+00:00'
        with client.session_transaction() as s:s['vault']=self.vault.token
        for lang,title in [('ru','Экономика и инвестиционный цикл'),('en','Economy and investment cycle')]:
            self.s['change_language'].execute(lang);before=self.dump()
            for chart in ('gdp','curve','phases'):
                response=client.get('/macroeconomics/dashboard?chart='+chart);self.assertEqual(response.status_code,200);body=response.get_data(as_text=True)
                self.assertIn(title,body);self.assertIn('aria-live="polite"',body);self.assertIn('id="macro-chart-data"',body)
                self.assertIn('aria-current=page',body);self.assertIn('/macroeconomics/dashboard',body)
            self.assertEqual(before,self.dump())
        form=dict(as_of='2026-10-31',mode='known',window='3',chart='cpi',kind='key_rate')
        with patch.object(runtime.services['macro_scheduler'],'run',return_value=4) as run:
            response=client.post('/macroeconomics/dashboard/refresh',data=form)
            run.assert_called_once_with('key_rate','2026-11-15','2026-11-21',self.vault.token)
            self.assertEqual(response.status_code,302);self.assertIn('as_of=2026-10-31',response.location);self.assertIn('window=3',response.location)
        for error in (RefreshError('Обновление уже выполняется'),RuntimeError('offline')):
            with patch.object(runtime.services['macro_scheduler'],'run',side_effect=error):
                response=client.post('/macroeconomics/dashboard/refresh',data=form,follow_redirects=True)
                self.assertEqual(response.status_code,200);self.assertIn('Economy and investment cycle',response.get_data(as_text=True))
        def lock(*args):self.vault.lock()
        with patch.object(runtime.services['macro_scheduler'],'run',side_effect=lock):
            self.assertIn('/unlock',client.post('/macroeconomics/dashboard/refresh',data=form).location)
        self.vault.unlock('password123')
        with client.session_transaction() as s:s['vault']=self.vault.token
        for query in ('page=bad','window=2','chart=x','mode=x','as_of=2099-01-01'):self.assertEqual(client.get('/macroeconomics/dashboard?'+query).status_code,400)
        self.assertEqual(client.post('/macroeconomics/dashboard/refresh',data=dict(form,kind='rosstat')).status_code,400)


class ChartDataTests(unittest.TestCase):
    def chart(self,data,kind,roles,**kw):
        cfg=defaults();cfg['series'].update(roles);return history([dict(r,source_title='Synthetic source') for r in data],validate(cfg),'2024-02-20T12:00:00+00:00','known',kind,kw.get('start','2010-01-01'))
    def test_gap_breaks_and_zero_are_preserved(self):
        values=[dict(date='2024-01-31',value=D(0)),dict(date='2024-03-31',value=D(-1))]
        p=list(with_breaks(values,'monthly'));self.assertFalse(p[0]['break_before']);self.assertTrue(p[1]['break_before']);self.assertEqual(p[0]['value'],0)
        p=list(with_breaks([dict(date='2024-01-01',value=D(0)),dict(date='2024-01-10',value=D(1))],'daily'));self.assertTrue(p[1]['break_before'])
    def test_growth_is_never_labelled_as_real_gdp_level_and_empty_gap_is_explained(self):
        data=[dict(row('gdp_volume_yoy_index',n=i,value=103+i),unit='index',base_year=None,adjustment='nsa') for i in range(24)]
        r=self.chart(data,'gdp',{'gdp':1});self.assertEqual(r['unit'],'percent');self.assertEqual(r['points'][-1]['value'],26);self.assertEqual(r['label'],'ВВП: рост год к году')
        self.assertEqual(self.chart(data,'gap',{'gdp':1})['reason'],'metadata')
        data=[dict(row(n=23),base_year=None)];self.assertEqual(self.chart(data,'gdp',{'gdp':1})['reason'],'metadata')
        data=[row(n=23,value=-1)];self.assertEqual(self.chart(data,'gdp',{'gdp':1})['reason'],'nonpositive')
        data=[dict(row(n=23),methodology='unknown')];self.assertEqual(self.chart(data,'gdp',{'gdp':1})['reason'],'metadata')
    def test_cpi_warmup_range_and_long_daily_history(self):
        data=monthly(count=13);r=self.chart(data,'cpi',{'cpi':1},start='2024-01-01');self.assertEqual(len(r['points']),1)
        self.assertEqual(r['points'][0]['date'],'2024-01-31');self.assertEqual(r['unit'],'percent')
        self.assertEqual(self.chart(data,'cpi',{'cpi':1},start='2024-02-01')['reason'],'range')
        self.assertEqual(self.chart(data[:3],'cpi',{'cpi':1})['reason'],'short')
    def test_curve_and_spread_do_not_join_incompatible_dates_or_sources(self):
        one=daily('ofz_zero_1y',['2024-02-19'],[10],series_id=1)
        two=daily('ofz_zero_2y',['2024-02-18'],[9],series_id=2)
        ten=daily('ofz_zero_10y',['2024-02-19'],[8],series_id=3)
        r=self.chart(one+two+ten,'curve',dict(yield_1y=1,yield_2y=2,yield_10y=3))
        self.assertEqual([p['value'] for p in r['points']],[10,None,8])
        r=self.chart(two+ten,'spread',dict(yield_2y=2,yield_10y=3));self.assertEqual(r['reason'],'no_overlap')
        two[0].update(period_start='2024-02-19',period_end='2024-02-19',source_id=2)
        r=self.chart(two+ten,'spread',dict(yield_2y=2,yield_10y=3));self.assertEqual(r['reason'],'metadata')
        two[0]['source_id']=1;r=self.chart(two+ten,'spread',dict(yield_2y=2,yield_10y=3));self.assertEqual(r['points'][0]['value'],-1)
        self.assertEqual(self.chart([],'curve',{})['reason'],'short')
