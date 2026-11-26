"""M7 application/adapter integration: as-of, configuration and durable evidence."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date,timedelta
from calendar import monthrange
from decimal import Decimal as D
from pathlib import Path
import json
import re
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_research as fixtures
from test_macro_derived import row,daily
from wealth_manager.modules.economic_cycle.application import Cycle,digest
from wealth_manager.modules.economic_cycle.config import defaults,validate,FEATURES,GROUPS,ROLES
from wealth_manager.modules.macroeconomics.calculations import canonical
from wealth_manager.modules.macroeconomics.data import Indicator,Source,Series,Artifact,Point
from wealth_manager.adapters.macroeconomics.session import Session
from wealth_manager.adapters.persistence.macro_data import Reader
from wealth_manager.factory import create_app
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound


class CycleTests(unittest.TestCase):
    setUp=fixtures.ResearchTests.setUp
    dump=fixtures.ResearchTests.dump
    def service(self): return Cycle(Session(self.vault),lambda:'2026-11-22T12:00:00+00:00')
    def ingest(self,rows,publication=True):
        first=rows[0];code=first['indicator'];points=[]
        for r in rows:
            published=r.get('published_at',str(date.fromisoformat(r['period_end'])+timedelta(days=1))) if publication else ''
            points.append(Point(r['period_start'],r['period_end'],D(r['value']),published,'date' if publication else 'unknown',r.get('status','final')))
        with patch('wealth_manager.adapters.persistence.macro_data.utcnow',return_value='2026-11-22T01:00:00+00:00'):
            self.s['macro_data'].ingest(Indicator(code,code,code),Source('cycle_test','Cycle test'),
                Series(code,'RU',code,first['unit'],first['frequency'],adjustment=first['adjustment'],base_year=first['base_year'],methodology='cycle-fixture-v1'),
                Artifact('synthetic.csv',canonical(rows).encode(),'cycle-test-v1'),points)
        return next(r['series_id'] for r in Reader(self.vault).read() if r['indicator']==code)
    def seed(self):
        groups={'gdp':[row(n=i,value=D(100)*D('1.008')**i) for i in range(35)]}
        specs=[('cpi','cpi_yoy_growth','percent','nsa',D('4.2')),('cpi_sa','cpi_mom_sa_growth','percent','sa',D('.33')),
               ('core','core_cpi_mom_index','index','nsa',D('100.33')),('expectations','inflation_expectations_12m','percent','nsa',D(4)),
               ('industry','industrial_yoy_index','index','nsa',D(104)),('unemployment','unemployment_rate','percent','nsa',D(4))]
        for role,code,unit,adjustment,v in specs:
            data=[]
            for i in range(22):
                y,m=2025+i//12,1+i%12
                data.append(row(code,n=i,value=v,unit=unit,frequency='monthly',adjustment=adjustment,base_year=None,period_start=f'{y}-{m:02d}-01',period_end=str(date(y,m,monthrange(y,m)[1]))))
            groups[role]=data
        days=['2026-02-20','2026-05-20','2026-08-20','2026-11-20']
        for role,code,values in [('key_rate','key_rate',[8,8,8,8]),('yield_10y','ofz_zero_10y',[7,7,7,7]),('yield_2y','ofz_zero_2y',[6.2]*4),('yield_1y','ofz_zero_1y',[6]*4),('equities','moex_imoex',[100,106,113,120]),('bonds','moex_rgbitr',[100,101,102,103]),('money','moex_rusfar',[8]*4)]:
            groups[role]=daily(code,days,values)
        cfg=defaults();cfg['series'].update({role:self.ingest(rows) for role,rows in groups.items()})
        self.service().configure(self.vault.token,cfg,0)
        return cfg,groups

    def test_empty_preview_is_read_only_and_diagnostic_run_can_be_saved(self):
        service=self.service();before=self.dump();p=service.preview(self.vault.token,as_of='2026-11-22')
        self.assertEqual(before,self.dump());self.assertEqual(p['result']['phase'],'INSUFFICIENT_DATA');self.assertEqual(p['result']['inputs'],[]);self.assertIsNone(p['result']['candidate'])
        rid=service.save(self.vault.token,expected_fingerprint=p['fingerprint'],as_of='2026-11-22')
        self.assertEqual(rid,service.save(self.vault.token,as_of='2026-11-22'))
        self.assertEqual(service.saved(self.vault.token,rid)['phase'],'INSUFFICIENT_DATA')
        with self.assertRaises(EntityNotFound): service.saved(self.vault.token,99999)
        for kwargs in (dict(as_of='2026-11-23'),dict(as_of='2026-11-22T13:00:00+00:00'),dict(mode='bad'),dict(as_of='bad')):
            with self.assertRaises(RecordError):service.preview(self.vault.token,**kwargs)

    def test_complete_snapshot_and_separately_selected_previous_vintage(self):
        cfg,groups=self.seed();service=self.service();before=self.dump();p=service.preview(self.vault.token,as_of='2026-11-22');r=p['result']
        self.assertEqual(self.dump(),before);self.assertEqual(r['phase'],'EARLY_EXPANSION');self.assertEqual(r['coverage'],100)
        self.assertEqual(r['features']['gdp_yoy']['date'],'2026-09-30');self.assertEqual(r['previous_features']['gdp_yoy']['date'],'2026-06-30')
        self.assertEqual(r['transition'],'confirmed');self.assertEqual(r['config_version'],digest(r['config']))
        self.assertTrue(set(v for f in r['features'].values() for v in f['revisions'])<=set(r['inputs']))
        # A new revision is known now, never leaked into the previous snapshot.
        revision=dict(groups['gdp'][-2],value=D(160),published_at='2026-11-21',status='revised');self.ingest([revision])
        after=service.preview(self.vault.token,as_of='2026-11-22')['result']
        self.assertEqual(after['previous_features'],r['previous_features']);self.assertNotEqual(after['features']['gdp_qoq'],r['features']['gdp_qoq'])
        latest=service.preview(self.vault.token,as_of='2026-11-22',mode='latest')['result']
        self.assertNotEqual(latest['previous_features'],r['previous_features']);self.assertIn('latest',latest['warnings'])

    def test_unknown_publication_not_backdated(self):
        series=self.ingest([row(n=i,value=100+i) for i in range(35)],False)
        cfg=defaults();cfg['series']['gdp']=series;service=self.service();service.configure(self.vault.token,cfg,0)
        old=service.preview(self.vault.token,as_of='2026-11-21')['result'];self.assertEqual(old['inputs'],[])
        now=service.preview(self.vault.token,as_of='2026-11-22')['result'];self.assertIn('unknown_publication',now['warnings'])
        self.assertTrue(now['inputs']);self.assertEqual(now['previous_features']['gdp_yoy']['value'],None)

    def test_saved_result_survives_settings_revision_and_vault_reopen(self):
        cfg,groups=self.seed();service=self.service();p=service.preview(self.vault.token,as_of='2026-11-21')
        rid=service.save(self.vault.token,expected_fingerprint=p['fingerprint'],as_of='2026-11-21');saved=service.saved(self.vault.token,rid)
        cfg['groups']['markets']='25';service.configure(self.vault.token,cfg,p['config_id'])
        self.ingest([dict(groups['gdp'][-1],value=D(150),published_at='2026-11-22',status='revised')])
        self.assertEqual(saved,service.saved(self.vault.token,rid))
        for table in ('economic_cycle_runs','economic_cycle_configs','economic_cycle_inputs'):
            with self.assertRaises(sqlite3.IntegrityError):self.vault.con.execute(f'UPDATE {table} SET rowid=rowid')
            with self.assertRaises(sqlite3.IntegrityError):self.vault.con.execute(f'DELETE FROM {table}')
            self.vault.con.rollback()
        self.vault.lock();self.vault.unlock('password123');self.assertEqual(saved,service.saved(self.vault.token,rid))
        self.assertEqual(self.service().settings(self.vault.token)['config']['groups'],validate(cfg)['groups'])
        self.assertEqual(len(saved['inputs']),self.vault.con.execute('SELECT count(*) FROM economic_cycle_inputs').fetchone()[0])

    def test_configuration_normalizes_restores_keeps_sources_and_rejects_stale_form(self):
        cfg,_=self.seed();service=self.service();state=service.settings(self.vault.token);old=state['config_id']
        cfg['groups']['markets']='50';new=service.configure(self.vault.token,cfg,old)
        self.assertNotEqual(old,new)
        with self.assertRaises(RecordError):service.configure(self.vault.token,cfg,old)
        with self.assertRaises(RecordError):service.configure(self.vault.token,cfg,True)
        service.configure(self.vault.token,None,new,True)
        state=service.settings(self.vault.token);self.assertEqual(state['config']['groups'],validate(defaults())['groups'])
        self.assertEqual(state['config']['series'],cfg['series'])
        self.assertEqual(state['config_id'],old)
        bad=deepcopy(cfg);bad['series']['gdp']=bad['series']['equities'];before=self.dump()
        with self.assertRaises(RecordError):service.configure(self.vault.token,bad,old)
        self.assertEqual(before,self.dump())

    def test_disk_failure_restores_configuration_run_and_encrypted_file(self):
        service=self.service();before=self.dump();encrypted=Path(self.vault.path).read_bytes()
        for action in (lambda:service.configure(self.vault.token,defaults(),0),lambda:service.save(self.vault.token,as_of='2026-11-22')):
            with patch('vault.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):action()
            self.assertEqual(before,self.dump());self.assertEqual(encrypted,Path(self.vault.path).read_bytes());self.assertFalse(self.vault.dirty())

    def test_revision_configuration_and_vault_generation_races_are_rejected(self):
        service=self.service();token=self.vault.token;original=service.session.write
        for what in ('revision','config','configure'):
            @contextmanager
            def racing(t):
                with original(t) as uow:
                    if what=='revision':
                        with patch.object(uow.macro_data,'revision_token',return_value=999):yield uow
                    else:
                        with patch.object(uow.economic_cycle,'state',return_value=dict(config_id=999)):yield uow
            with patch.object(service.session,'write',racing):
                with self.assertRaises(RecordError):
                    if what=='configure':service.configure(token,defaults(),0)
                    else:service.save(token,as_of='2026-11-22')
        with self.assertRaises(RecordError):service.save(token,expected_fingerprint='stale',as_of='2026-11-22')
        self.vault.lock()
        for action in (lambda:service.preview(token),lambda:service.saved(token,1),lambda:service.settings(token)):
            with self.assertRaises(RecordError):action()
        self.vault.unlock('password123')
        with self.assertRaises(RecordError):service.preview(token)

    def test_http_settings_localization_preview_save_and_validation(self):
        cfg,_=self.seed();app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        app.extensions['wealth_manager'].services['economic_cycle'].clock=lambda:'2026-11-22T12:00:00+00:00'
        with client.session_transaction() as session:session['vault']=self.vault.token
        for language,heading in [('ru','Экономический цикл'),('en','Economic cycle')]:
            self.s['change_language'].execute(language)
            for url in ('/macroeconomics/cycle?as_of=2026-11-22','/settings/economic-cycle'):
                before=self.dump();response=client.get(url);self.assertEqual(response.status_code,200);self.assertEqual(before,self.dump())
                self.assertIn(heading,response.get_data(as_text=True))
        service=self.service();p=service.preview(self.vault.token,as_of='2026-11-22')
        response=client.post('/macroeconomics/cycle',data=dict(as_of=p['result']['as_of'],mode='known',fingerprint=p['fingerprint']))
        self.assertEqual(response.status_code,302);page=client.get(response.location);self.assertIn('Saved calculation',page.get_data(as_text=True))
        self.assertEqual(client.get('/macroeconomics/cycle/9999').status_code,404)
        for data in ({},dict(fingerprint='bad',as_of='2026-11-22')):self.assertEqual(client.post('/macroeconomics/cycle',data=data).status_code,400)
        for query in ('mode=bad','as_of=bad','as_of=2999-01-01'):self.assertEqual(client.get('/macroeconomics/cycle?'+query).status_code,400)
        form=dict(config_id=p['config_id'],method='hp',window='40',real_rate_basis='yield_1y')
        form.update({'series_'+k:str(v) if v else '' for k,v in cfg['series'].items()});form.update({'group_'+k:v for k,v in cfg['groups'].items()})
        for k,f in cfg['features'].items():
            form['enabled_'+k]='on'
            for field in ('weight','center','scale'):form[field+'_'+k]=f[field]
        response=client.post('/settings/economic-cycle',data=form);self.assertEqual(response.status_code,302);self.assertEqual(client.get(response.location).status_code,200)
        response=client.post('/settings/economic-cycle',data=dict(config_id=service.settings(self.vault.token)['config_id'],action='restore'));self.assertEqual(response.status_code,302)
        for data in ({},dict(form,weight_gdp_yoy='-1'),dict(form,series_gdp='99999')):
            self.assertEqual(client.post('/settings/economic-cycle',data=data).status_code,400)
        self.vault.lock();self.assertEqual(client.get('/macroeconomics/cycle').status_code,302)

    def test_schema17_migration_is_atomic_and_preserves_old_data(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        with patch('db._migrate_economic_cycle'),patch('db._migrate_market_catalog'),patch('db._migrate_sector_scoring'):db.init_db(con)
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],17)
        before=list(con.iterdump());original=db._migrate_economic_cycle
        def fail(c):original(c);raise RuntimeError('failed migration')
        with patch('db._migrate_economic_cycle',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(con)
        self.assertEqual(before,list(con.iterdump()))
        db.init_db(con);self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],db.SCHEMA_VERSION)
        self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
        before=list(con.iterdump());db.init_db(con);self.assertEqual(before,list(con.iterdump()))
