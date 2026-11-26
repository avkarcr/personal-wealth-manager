"""M10 integration: guarded vault writes, migration, HTTP and frozen evidence."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import ast
import json
import re
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_research as fixtures
from test_sector_scoring_model import CUTOFF, observation, small_config
from wealth_manager.adapters.macroeconomics.session import Session
from wealth_manager.adapters.persistence.sector_scoring import Repository
from wealth_manager.modules.sector_scoring.application import Scoring
from wealth_manager.modules.sector_scoring.config import METRICS, AUTO, defaults, profile_rule
from wealth_manager.modules.sector_scoring.domain import Observation
from wealth_manager.modules.economic_cycle.config import defaults as cycle_defaults
from wealth_manager.modules.market_catalog.domain import Sector
from wealth_manager.modules.macroeconomics.refresh import RefreshError
from wealth_manager.modules.macroeconomics.calculations import canonical
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.factory import create_app


class SectorScoringTests(unittest.TestCase):
    setUp_base=fixtures.ResearchTests.setUp
    dump=fixtures.ResearchTests.dump

    def setUp(self):
        self.setUp_base()
        self.service=Scoring(Session(self.vault),lambda:CUTOFF)
        self.sid=next(s['id'] for s in Repository(self.vault.con).sectors() if s['code']=='technology')
        self.app=create_app({'TESTING':True},vault=self.vault);self.client=self.app.test_client()
        self.app.extensions['wealth_manager'].services['sector_scoring'].clock=lambda:CUTOFF
        self.app.extensions['wealth_manager'].services['macro_dashboard'].clock=lambda:CUTOFF
        with self.client.session_transaction() as session:session['vault']=self.vault.token

    def item(self,metric='pe',value=10,**changes):
        row=observation(metric,value,sector_id=self.sid,**changes)
        return Observation(**{k:v for k,v in row.items() if k not in ('id','received_at','supersedes_id')})

    def configure(self,cfg=None):
        return self.service.configure(self.vault.token,cfg or small_config(),self.service.settings(self.vault.token)['config_id'])

    def seed(self):
        self.configure()
        for k,v in [('revenue_growth',10),('pe',10),('liquidity',100)]:self.service.observe(self.vault.token,self.item(k,v))
        return self.service.preview(self.vault.token)

    def detail(self,result):return next(r for r in result['sectors'] if r['sector']['id']==self.sid)

    def test_reopen_frozen_results_idempotency_and_independent_cycle_settings(self):
        cycle_before=Session(self.vault).cycle_snapshot(self.vault.token)['config'];wealth=self.s['reports'].dashboard()
        p=self.seed();self.assertEqual(self.detail(p['result'])['score'],70)
        before=self.dump();again=self.service.preview(self.vault.token);self.assertEqual(before,self.dump())
        self.assertEqual(p['fingerprint'],again['fingerprint'])
        rid=self.service.save(self.vault.token,p['fingerprint'])
        self.assertEqual(self.service.save(self.vault.token,p['fingerprint']),rid)
        self.assertEqual(canonical(self.service.saved(self.vault.token,rid)),canonical(p['result']))
        self.configure(dict(small_config(),min_liquidity='1000'))
        self.assertEqual(self.detail(self.service.preview(self.vault.token)['result'])['reason'],'liquidity')
        self.assertEqual(self.detail(self.service.saved(self.vault.token,rid))['score'],'70')
        self.assertEqual(Session(self.vault).cycle_snapshot(self.vault.token)['config'],cycle_before)
        self.assertEqual(self.s['reports'].dashboard(),wealth)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.detail(self.service.saved(self.vault.token,rid))['score'],'70')
        self.assertEqual(len(Repository(self.vault.con).observations()),3)

    def test_input_revisions_publication_and_withdrawal(self):
        item=self.item(published_at='2026-11-21')
        rid=self.service.observe(self.vault.token,item)
        self.assertEqual(self.service.observe(self.vault.token,item),rid)
        newer=replace(item,value='25',published_at='2026-11-25')
        revision=self.service.observe(self.vault.token,newer)
        self.assertGreater(revision,rid)
        first=self.detail(self.service.preview(self.vault.token,as_of='2026-11-23')['result'])['features']['pe']
        self.assertEqual(first['value'],'10')
        latest=self.detail(self.service.preview(self.vault.token,as_of='2026-11-23',mode='latest')['result'])['features']['pe']
        self.assertEqual(latest['value'],'25');self.assertEqual(latest['observation']['supersedes_id'],rid)
        with self.assertRaises(RecordError):self.service.observe(self.vault.token,replace(newer,value='15',published_at='2026-11-22'))
        self.service.observe(self.vault.token,replace(newer,value=None,status='unavailable',published_at=''))
        self.assertEqual(self.detail(self.service.preview(self.vault.token)['result'])['features']['pe']['reason'],'unavailable')
        with self.assertRaises(RecordError):self.service.observe(self.vault.token,item,expected_revision=0)
        unknown=self.item('liquidity',100,published_at='')
        self.service.observe(self.vault.token,unknown)
        self.assertEqual(self.detail(self.service.preview(self.vault.token,as_of='2026-11-25')['result'])['features']['liquidity']['reason'],'missing')

    def test_cycle_factor_weights_do_not_disable_sector_macro_evidence(self):
        import test_economic_cycle as cycle_fixtures
        fixture=cycle_fixtures.CycleTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        cfg,_=fixture.seed()
        service=Scoring(Session(fixture.vault),lambda:'2026-11-22T12:00:00+00:00')
        before=service.preview(fixture.vault.token)['result']
        self.assertIsNotNone(before['macro_evidence']['key_change']['value'])
        cfg['features']['key_change']['enabled']=False
        cycle=fixture.service();cycle.configure(fixture.vault.token,cfg,cycle.settings(fixture.vault.token)['config_id'])
        after=service.preview(fixture.vault.token)['result']
        self.assertIsNone(after['cycle']['features']['key_change']['value'])
        self.assertEqual(before['macro_evidence']['key_change'],after['macro_evidence']['key_change'])
        self.assertEqual(before['config'],after['config'])

    def test_optimistic_settings_and_preview_validation(self):
        cid=self.configure();before=self.dump()
        for expected in (0,False,'1'):
            with self.assertRaises(RecordError):self.service.configure(self.vault.token,defaults(),expected)
        self.assertEqual(before,self.dump())
        self.service.configure(self.vault.token,{},cid,restore=True)
        for params in (dict(mode='bad'),dict(as_of='2027-01-01'),dict(as_of='bad')):
            with self.assertRaises(RecordError):self.service.preview(self.vault.token,**params)
        with self.assertRaises(RecordError):self.service.save(self.vault.token,'stale')
        with self.assertRaises(EntityNotFound):self.service.saved(self.vault.token,999)

    def test_save_rechecks_all_dependencies_after_preview(self):
        self.seed();original=self.service.preview
        changes=[lambda:self.service.observe(self.vault.token,self.item(value=12,published_at='2026-11-25')),
                 lambda:self.configure(dict(small_config(),min_coverage='70')),
                 lambda:self.s['macroeconomics'].save(self.observation),
                 lambda:self.s['economic_cycle'].configure(self.vault.token,cycle_defaults(),0),
                 lambda:self.s['market_catalog'].save_sector(Sector('added','Added'))]
        for change in changes:
            def raced(*args,**kwargs):
                result=original(*args,**kwargs);change();return result
            with patch.object(self.service,'preview',side_effect=raced),self.assertRaises(RecordError):self.service.save(self.vault.token)
        self.assertEqual(Repository(self.vault.con).recent(),[])

    def test_disk_failure_rolls_back_input_settings_and_saved_results(self):
        self.seed()
        for action in (lambda:self.service.observe(self.vault.token,self.item('pe',30,published_at='2026-11-25')),
                       lambda:self.configure(dict(small_config(),min_coverage='70')),
                       lambda:self.service.save(self.vault.token)):
            before=self.dump();disk=Path(self.vault.path).read_bytes()
            with patch('vault.os.replace',side_effect=OSError('disk full')),self.assertRaises(OSError):action()
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk);self.assertFalse(self.vault.dirty())

    def test_sector_reference_archiving_and_vault_generation(self):
        self.service.observe(self.vault.token,self.item())
        with self.assertRaises(RecordError):self.s['market_catalog'].delete_sector(self.sid)
        sector=next(r for r in Repository(self.vault.con).sectors() if r['id']==self.sid)
        self.s['market_catalog'].save_sector(Sector(**dict(sector,archived=True)))
        with self.assertRaises(RecordError):self.service.observe(self.vault.token,self.item('pe',12))
        with self.assertRaises(EntityNotFound):self.service.observe(self.vault.token,replace(self.item(),sector_id=999))
        old=self.vault.token;self.vault.lock();self.vault.unlock('password123')
        for action in (lambda:self.service.preview(old),lambda:self.service.saved(old,1),lambda:self.service.observe(old,self.item())):
            with self.assertRaises(RefreshError):action()

    def test_immutable_inputs_and_calculation_evidence(self):
        self.seed();self.service.save(self.vault.token)
        for table in ('sector_revisions','sector_configs','sector_runs','sector_run_inputs'):
            for query in (f'DELETE FROM {table}',f'UPDATE {table} SET rowid=rowid'):
                with self.assertRaises(sqlite3.IntegrityError):self.vault.con.execute(query)
                self.vault.con.rollback()
        self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_schema19_migration_is_atomic_idempotent_and_preserves_catalog(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        with patch('db._migrate_sector_scoring'):db.init_db(con)
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],19)
        before=list(con.iterdump());original=db._migrate_sector_scoring
        def fail(c):original(c);raise OSError('failed')
        with patch('db._migrate_sector_scoring',side_effect=fail),self.assertRaises(OSError):db.init_db(con)
        self.assertEqual(list(con.iterdump()),before)
        db.init_db(con);self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],20)
        self.assertEqual(con.execute('SELECT count(*) FROM sectors').fetchone()[0],10)
        self.assertEqual(con.execute('SELECT count(*) FROM sector_revisions').fetchone()[0],0)
        before=list(con.iterdump());db.init_db(con);self.assertEqual(list(con.iterdump()),before)

    def test_http_readonly_pages_localization_errors_and_frozen_details(self):
        p=self.seed()
        for language,heading in [('ru','Отраслевая привлекательность'),('en','Sector attractiveness')]:
            self.s['change_language'].execute(language)
            for url in ('/macroeconomics/sectors',f'/macroeconomics/sectors/{self.sid}','/settings/sector-scoring',f'/settings/sector-scoring/{self.sid}','/macroeconomics/dashboard'):
                before=self.dump();response=self.client.get(url);self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:300])
                self.assertEqual(before,self.dump())
            self.assertIn(heading,self.client.get('/macroeconomics/sectors').get_data(as_text=True))
        response=self.client.post('/macroeconomics/sectors/save',data=dict(fingerprint=p['fingerprint'],as_of=p['result']['as_of'],mode='known'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get(response.location).status_code,200)
        self.assertEqual(self.client.get(response.location+f'/{self.sid}').status_code,200)
        for url in ('/macroeconomics/sectors/999','/macroeconomics/sectors/runs/999','/settings/sector-scoring/999'):
            self.assertEqual(self.client.get(url).status_code,404)
        for query in ('mode=bad','as_of=2999-01-01'):
            self.assertEqual(self.client.get('/macroeconomics/sectors?'+query).status_code,400)
        self.assertEqual(self.client.post('/macroeconomics/sectors/save',data={}).status_code,400)
        self.vault.lock();self.assertEqual(self.client.get('/macroeconomics/sectors').status_code,302)

    def test_http_observation_validation_escaping_and_source_provenance(self):
        item=self.item();form=dict(item.__dict__,revision_token=0,source='<script>untrusted</script>')
        response=self.client.post(f'/macroeconomics/sectors/{self.sid}/observations',data=form)
        self.assertEqual(response.status_code,302)
        body=self.client.get(response.location).get_data(as_text=True)
        self.assertIn('&lt;script&gt;untrusted&lt;/script&gt;',body);self.assertNotIn('<script>untrusted</script>',body)
        self.assertIn('Synthetic equal issuer aggregate',body)
        for data in ({},dict(form,metric='bad'),dict(form,revision_token=1,value='NaN'),dict(form,revision_token=1,population_size=0)):
            self.assertEqual(self.client.post(f'/macroeconomics/sectors/{self.sid}/observations',data=data).status_code,400)

    def test_http_global_and_sector_settings_save_restore_inherit(self):
        state=self.service.settings(self.vault.token);cfg=state['config']
        form={k:cfg[k] for k in ('min_coverage','min_sample','min_liquidity')};form['config_id']=state['config_id']
        form.update({'group_'+g:v for g,v in cfg['groups'].items()})
        for code,f in cfg['features'].items():
            form['enabled_'+code]='on'
            form.update({k+'_'+code:v for k,v in f.items() if k!='enabled'})
        self.assertEqual(self.client.post('/settings/sector-scoring',data=form).status_code,302)
        self.assertEqual(self.client.post('/settings/sector-scoring',data=form).status_code,400)
        path=f'/settings/sector-scoring/{self.sid}'
        state=self.service.settings(self.vault.token);rule=state['rules'][self.sid]
        form=dict(config_id=state['config_id'],sector_enabled='on',profile='general',not_applicable='commodity',override_pe='on',bad_pe='30',good_pe='5')
        form.update({'phase_'+p:v for p,v in rule['phases'].items()});form.update({'sensitivity_'+p:v for p,v in rule['sensitivity'].items()})
        self.assertEqual(self.client.post(path,data=form).status_code,302)
        self.assertEqual(self.service.settings(self.vault.token)['rules'][self.sid]['bounds']['pe']['bad'],'30')
        for action,profile in [('profile','gold'),('inherit','general')]:
            state=self.service.settings(self.vault.token)
            self.assertEqual(self.client.post(path,data=dict(config_id=state['config_id'],action=action,profile=profile)).status_code,302)
        self.assertNotIn(str(self.sid),self.service.settings(self.vault.token)['config']['sectors'])
        self.assertEqual(self.client.post(path,data=dict(config_id=0,profile='bad')).status_code,400)
        cid=self.service.settings(self.vault.token)['config_id']
        self.assertEqual(self.client.post('/settings/sector-scoring',data=dict(config_id=cid,action='restore')).status_code,302)


if __name__=='__main__':unittest.main()
