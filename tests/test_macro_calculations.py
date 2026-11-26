"""M6 integration: as-of, exact vintages, migration, atomic encrypted runs and RU/EN."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import json
import re
import sqlite3
import unittest
import db
import test_research as fixtures
from test_macro_derived import row, metrics
from wealth_manager.modules.macroeconomics.calculations import Calculations
from wealth_manager.modules.macroeconomics.data import Indicator,Source,Series,Artifact,Point
from wealth_manager.adapters.macroeconomics.session import Session
from wealth_manager.adapters.persistence.macro_data import Reader
from wealth_manager.factory import create_app
from wealth_manager.shared.records import RecordError


class CalculationTests(unittest.TestCase):
    setUp=fixtures.ResearchTests.setUp
    dump=fixtures.ResearchTests.dump
    def seed(self,count=24,adjustment='sa',base=2021,source='test_m6',values=None,publication=True,revision='final'):
        points=[]
        for i in range(count):
            r=row(n=i,value=100+i if values is None else values[i]);end=date.fromisoformat(r['period_end'])
            points.append(Point(r['period_start'],r['period_end'],r['value'],str(end+timedelta(days=45)) if publication else '', 'date' if publication else 'unknown',revision))
        with patch('wealth_manager.adapters.persistence.macro_data.utcnow',return_value='2026-11-22T01:00:00+00:00'):
            self.s['macro_data'].ingest(Indicator('gdp_real_level_sa','ВВП','GDP'),Source(source,'Test '+source),
                Series('gdp_real_level_sa','RU','fixture','RUB','quarterly',adjustment=adjustment,base_year=base,methodology='test-v1'),
                Artifact('fixture.csv',('synthetic '+str(values)).encode(),'test-v1'),points)
        return next(r['series_id'] for r in Reader(self.vault).read() if r['source_title']=='Test '+source and r['base_year']==base and r['adjustment']==adjustment)
    def service(self):return Calculations(Session(self.vault),lambda:'2026-11-22T12:00:00+00:00')
    def params(self,sid,**kw):return dict(primary=sid,as_of='2026-11-22',**kw)

    def test_preview_read_only_and_asof_excludes_future_periods_and_publications(self):
        sid=self.seed();s=self.service();before=self.dump();result=s.preview(self.vault.token,**self.params(sid))['result']
        self.assertEqual(before,self.dump());self.assertEqual(len(result['inputs']),24)
        self.assertEqual(result['version'],'macro-derived-v1');self.assertTrue(metrics(result)['gap']['points'])
        then=s.preview(self.vault.token,primary=sid,as_of='2022-01-01')['result']
        self.assertEqual(len(then['inputs']),15);self.assertEqual(metrics(then)['gap']['reason'],'short')
        latest=s.preview(self.vault.token,primary=sid,as_of='2022-01-01',mode='latest')['result']
        self.assertEqual(len(latest['inputs']),16);self.assertIn('latest',latest['warnings'])
        with self.assertRaises(RecordError):s.preview(self.vault.token,primary=sid,as_of='2026-11-23')

    def test_unknown_publication_not_backdated_and_sources_bases_never_joined(self):
        first=self.seed(count=12,publication=False);second=self.seed(count=12,base=2020,source='second')
        s=self.service();then=s.preview(self.vault.token,primary=first,as_of='2025-12-31')['result']
        self.assertEqual(then['inputs'],[]);self.assertEqual(then['status'],'INSUFFICIENT_DATA')
        now=s.preview(self.vault.token,primary=first,secondary=second,as_of='2026-11-22')['result']
        self.assertEqual(metrics(now)['gap']['points'],[]);self.assertIn('unknown_publication',now['warnings'])
        self.assertEqual(len(now['series']),2)
        # Neither a second source nor a different base supplies missing quarters.
        self.assertEqual(len(metrics(now)['yoy']['points']),8)

    def test_immutable_saved_result_idempotence_reopen_and_later_revisions(self):
        sid=self.seed();s=self.service();params=self.params(sid);p=s.preview(self.vault.token,**params)
        rid=s.save(self.vault.token,expected_fingerprint=p['fingerprint'],**params)
        self.assertEqual(rid,s.save(self.vault.token,**params));saved=s.saved(self.vault.token,rid)
        self.assertEqual(len(saved['inputs']),24)
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_calculation_inputs').fetchone()[0],24)
        self.seed(values=[100+i if i<23 else 150 for i in range(24)],revision='revised')
        self.assertEqual(s.saved(self.vault.token,rid),saved)
        new=s.preview(self.vault.token,**params)
        self.assertNotEqual(new['fingerprint'],p['fingerprint'])
        with self.assertRaises(RecordError):s.save(self.vault.token,expected_fingerprint=p['fingerprint'],**params)
        self.assertNotEqual(s.save(self.vault.token,**params),rid)
        for table in ('macro_calculations','macro_calculation_inputs'):
            for sql in ('DELETE FROM '+table,'UPDATE '+table+' SET run_id=run_id' if table.endswith('inputs') else 'UPDATE '+table+' SET result=result'):
                with self.assertRaises(sqlite3.IntegrityError):self.vault.con.execute(sql)
                self.vault.con.rollback()
        self.vault.lock();self.vault.unlock('password123');self.assertEqual(s.saved(self.vault.token,rid),saved)
        self.assertNotIn(b'macro-derived-v1',Path(self.vault.path).read_bytes())

    def test_revision_available_after_cutoff_cannot_change_known_run(self):
        sid=self.seed();s=self.service();when='2024-01-01';params=dict(primary=sid,as_of=when)
        before=s.preview(self.vault.token,**params)
        r=row(n=20,value=999)
        self.s['macro_data'].ingest(Indicator('gdp_real_level_sa','ВВП','GDP'),Source('test_m6','Test test_m6'),
            Series('gdp_real_level_sa','RU','fixture','RUB','quarterly',adjustment='sa',base_year=2021,methodology='test-v1'),
            Artifact('revised.csv',b'revised','test-v1'),[Point(r['period_start'],r['period_end'],r['value'],'2026-01-01','date','revised')])
        self.assertEqual(s.preview(self.vault.token,**params)['fingerprint'],before['fingerprint'])
        self.assertNotEqual(s.preview(self.vault.token,mode='latest',**params)['result']['inputs'],before['result']['inputs'])

    def test_disk_error_rolls_back_run_and_inputs_and_source_data(self):
        sid=self.seed();s=self.service();before=self.dump();encrypted=Path(self.vault.path).read_bytes()
        with patch.object(self.vault,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):s.save(self.vault.token,**self.params(sid))
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)

    def test_relock_and_concurrent_revision_block_saving_snapshot(self):
        sid=self.seed();s=self.service();original=s.session.write
        @contextmanager
        def write(token):
            self.seed(values=[200+i for i in range(24)])
            with original(token) as uow:yield uow
        with patch.object(s.session,'write',write):
            with self.assertRaises(RecordError):s.save(self.vault.token,**self.params(sid))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_calculations').fetchone()[0],0)
        @contextmanager
        def relock(token):
            self.vault.lock();self.vault.unlock('password123')
            with original(token) as uow:yield uow
        with patch.object(s.session,'write',relock):
            with self.assertRaises(RecordError):s.save(self.vault.token,**self.params(sid))

    def test_invalid_parameters_empty_result_and_locked_reads(self):
        sid=self.seed(count=1);s=self.service()
        for kw in (dict(primary=0),dict(primary=999),dict(primary=sid,secondary=sid),dict(primary=True),dict(mode='other')):
            with self.assertRaises(RecordError):s.preview(self.vault.token,**kw)
        with self.assertRaises(RecordError):s.save(self.vault.token,**self.params(sid))
        self.assertEqual(s.preview(self.vault.token)['result']['status'],'INSUFFICIENT_DATA')
        token=self.vault.token;self.vault.lock()
        with self.assertRaises(RecordError):s.preview(token)
        with self.assertRaises(RecordError):s.saved(token,1)

    def test_http_preview_save_reopen_localization_and_changed_preview(self):
        sid=self.seed();app=create_app({'TESTING':True},vault=self.vault);client=app.test_client();service=self.service()
        app.extensions['wealth_manager'].services['macro_calculations']=service
        with client.session_transaction() as session:session['vault']=self.vault.token
        url=f'/macroeconomics/calculations?primary={sid}&as_of=2026-11-22'
        for language,heading in [('ru','Производные показатели'),('en','Derived indicators')]:
            self.s['change_language'].execute(language);before=self.dump();response=client.get(url)
            self.assertEqual(response.status_code,200);self.assertIn(heading,response.get_data(as_text=True));self.assertEqual(self.dump(),before)
        p=service.preview(self.vault.token,**self.params(sid));data=dict(self.params(sid),fingerprint=p['fingerprint'])
        response=client.post('/macroeconomics/calculations',data=data);self.assertEqual(response.status_code,302)
        result=client.get(response.location);self.assertEqual(result.status_code,200);self.assertIn('Saved calculation',result.get_data(as_text=True))
        self.assertEqual(client.get('/macroeconomics/calculations/99999').status_code,404)
        self.assertEqual(client.post('/macroeconomics/calculations',data=dict(data,fingerprint='stale')).status_code,400)
        for query in ('primary=bad','window=19','method=other','secondary=-1','as_of=bad'):
            self.assertEqual(client.get('/macroeconomics/calculations?'+query).status_code,400)
        self.vault.lock();self.assertEqual(client.get(url).status_code,302)

    def test_new_import_passports_preserve_sa_and_expectations_horizon(self):
        from test_macro_statistics import config,csvfile
        service=self.s['macro_import']
        for code in ('cpi_mom_sa_index','cpi_mom_sa_growth','inflation_expectations_12m'):
            files=[csvfile('period;value\n2024-01;1\n2024-02;2\n')];configs=[config(indicator=code)]
            preview=service.preview(files,configs,self.vault.token)
            service.confirm(files,configs,self.vault.token,preview['revision'])
        rows=Reader(self.vault).read()
        for r in rows:
            self.assertEqual(r['frequency'],'monthly')
            self.assertEqual(r['adjustment'],'nsa' if r['indicator']=='inflation_expectations_12m' else 'sa')
            self.assertEqual(r['unit'],'index' if r['indicator']=='cpi_mom_sa_index' else 'percent')

    def test_v16_migration_idempotence_and_failure_restores_original(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        with patch('db._migrate_macro_calculations'),patch('db._migrate_economic_cycle'),patch('db._migrate_market_catalog'),patch('db._migrate_sector_scoring'):db.init_db(c)
        self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],16)
        before=list(c.iterdump());original=db._migrate_macro_calculations
        def fail(con):original(con);raise RuntimeError('failure after migration')
        with patch('db._migrate_macro_calculations',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(c)
        self.assertEqual(list(c.iterdump()),before)
        db.init_db(c);self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],db.SCHEMA_VERSION)
        before=list(c.iterdump());db.init_db(c);self.assertEqual(list(c.iterdump()),before)
