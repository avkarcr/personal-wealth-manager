"""Allocation invariants, encrypted durability, migration and localized HTTP flows."""
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import db
from vault import Vault
from wealth_manager.bootstrap import build_services
from wealth_manager.factory import create_app
from wealth_manager.modules.accounts.domain import Account
from wealth_manager.modules.assets.domain import Asset
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.planning.domain import Goal, Portfolio, PlanningError, share_basis_points, valued_share
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.adapters.persistence.planning import SqlitePlanningRepository, SqlitePlanningReader


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.vault = Vault(str(Path(self.tmp.name)/'vault.enc'))
        db.init_db(self.vault.create('password123')); self.vault.save(force=True)
        self.addCleanup(self.vault.lock)
        self.s = build_services(self.vault); self.cmd = self.s['planning']; self.reports = self.s['planning_reports']
        self.aid = self.s['save_financial_account'].execute(Account('Broker'))
        self.hid = self.s['positions'].save(Position(self.aid, name='Stock', quantity=10, price=20))
        self.asset = self.s['save_asset'].execute(Asset('Home', current_value=Decimal('1000')))
        self.gid = self.cmd.save_goal(Goal('Retirement', Decimal('1000')))
        self.pid = self.cmd.save_portfolio(Portfolio('Long term', self.gid))

    def dump(self): return list(self.vault.con.iterdump())

    def test_allocation_progress_and_global_capital_do_not_double_count(self):
        before = self.s['reports'].dashboard()
        self.cmd.allocate(self.pid,'holding',self.hid,'50')
        self.cmd.allocate(self.pid,'asset',self.asset,'25')
        other = self.cmd.save_portfolio(Portfolio('Second', self.gid))
        self.cmd.allocate(other,'holding',self.hid,'50')
        goal = self.reports.overview()['goals'][0]
        self.assertEqual(goal['total'], Decimal('450'))
        self.assertEqual(goal['progress'], Decimal('45'))
        self.assertEqual(self.s['reports'].dashboard(), before)
        self.assertEqual(self.reports.detail(self.pid)['choices'][0]['available'], 50)
        self.cmd.allocate(self.pid,'holding',self.hid,'25')
        self.assertEqual(len(self.reports.detail(self.pid)['portfolio']['rows']),2)
        self.assertEqual(self.reports.overview()['goals'][0]['total'],400)
        self.s['positions'].save(Position(self.aid, id=self.hid, name='Stock', quantity=20, price=20))
        self.assertEqual(self.reports.overview()['goals'][0]['total'],550)

    def test_overallocation_is_rejected_atomically_for_holdings_and_assets(self):
        other = self.cmd.save_portfolio(Portfolio('Second', self.gid))
        for kind, rid in [('holding',self.hid),('asset',self.asset)]:
            self.cmd.allocate(self.pid,kind,rid,'99.99')
            self.cmd.allocate(other,kind,rid,'0.01')
            before = self.dump()
            with self.assertRaises(PlanningError): self.cmd.allocate(other,kind,rid,'0.02')
            self.assertEqual(self.dump(),before)
        for value in ('0','-1','100.01','0.001','NaN','Infinity','oops'):
            with self.subTest(value=value), self.assertRaises(PlanningError): share_basis_points(value)
        self.assertEqual(share_basis_points('33.33'),3333)

    def test_goal_edit_reassign_delete_and_reopen(self):
        self.cmd.allocate(self.pid,'asset',self.asset,'100')
        self.cmd.save_goal(Goal('Changed',Decimal('2000'),due_date='2030-01-01',id=self.gid))
        self.assertEqual(self.reports.overview()['goals'][0]['progress'],50)
        with self.assertRaises(PlanningError): self.cmd.delete_goal(self.gid)
        new = self.cmd.save_goal(Goal('Other',Decimal('100')))
        self.cmd.save_portfolio(Portfolio('Renamed',new,note='test',id=self.pid))
        self.cmd.delete_goal(self.gid)
        self.vault.lock(); self.vault.unlock('password123'); db.init_db(self.vault.con)
        p = self.reports.detail(self.pid)['portfolio']
        self.assertEqual((p['name'],p['goal_name'],p['total']),('Renamed','Other',1000))
        self.cmd.delete_portfolio(self.pid); self.cmd.delete_goal(new)
        self.assertEqual(self.reports.overview()['allocations'],[])
        self.assertEqual(self.s['reports'].dashboard()['total']['value'],1200)

    def test_missing_rates_and_archived_sources_do_not_overstate_progress(self):
        self.cmd.save_goal(Goal('FX',Decimal('10'),currency='ZZZ',id=self.gid))
        self.cmd.allocate(self.pid,'asset',self.asset,'100')
        self.assertIsNone(self.reports.overview()['goals'][0]['progress'])
        self.vault.con.execute("INSERT INTO rates(currency,rate) VALUES ('ZZZ',100)"); self.vault.con.commit()
        self.assertEqual(self.reports.overview()['goals'][0]['progress'],100)
        self.s['accounts'].toggle_archived(self.asset)
        self.assertEqual(self.reports.overview()['goals'][0]['total'],0)
        with self.assertRaises(PlanningError): self.cmd.allocate(self.pid,'asset',self.asset,'50')
        self.assertFalse(any(s['kind']=='asset' for s in self.reports.detail(self.pid)['choices']))
        self.s['accounts'].toggle_archived(self.asset)
        self.assertEqual(self.reports.overview()['goals'][0]['progress'],100)
        source=dict(active=True,currency='ZZZ',value=10)
        self.assertEqual(valued_share(source,5000,{},'ZZZ'),5)
        for rate in (0,-1,float('nan'),float('inf')):
            self.assertIsNone(valued_share(source,5000,{'ZZZ':rate,'RUB':1},'RUB'))

    def test_deleting_underlying_entities_requires_removing_allocations(self):
        self.cmd.allocate(self.pid,'holding',self.hid,'100')
        self.cmd.allocate(self.pid,'asset',self.asset,'100')
        for action in (lambda:self.s['positions'].delete(self.hid),lambda:self.s['accounts'].delete(self.aid),lambda:self.s['accounts'].delete(self.asset),lambda:self.s['positions'].save(Position(self.aid,id=self.hid,quantity=-1,price=20))):
            before=self.dump()
            with self.assertRaises(PlanningError): action()
            self.assertEqual(before,self.dump())
        for row in self.reports.detail(self.pid)['portfolio']['rows']:self.cmd.remove_allocation(self.pid,row['id'])
        self.s['positions'].delete(self.hid); self.s['accounts'].delete(self.asset)
        self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_invalid_inputs_and_cross_portfolio_deletion_are_rejected(self):
        for goal in (Goal('',Decimal(1)),Goal('X',Decimal(0)),Goal('X',Decimal('NaN')),Goal('X',Decimal(1),due_date='bad')):
            with self.assertRaises(PlanningError):self.cmd.save_goal(goal)
        for p in (Portfolio('',self.gid),Portfolio('X',0)):
            with self.assertRaises(PlanningError):self.cmd.save_portfolio(p)
        with self.assertRaises(EntityNotFound):self.cmd.save_portfolio(Portfolio('X',999))
        with self.assertRaises(EntityNotFound):self.cmd.save_goal(Goal('X',Decimal(1),id=999))
        with self.assertRaises(EntityNotFound):self.reports.detail(999)
        with self.assertRaises(EntityNotFound):self.cmd.allocate(self.pid,'holding',999,'1')
        with self.assertRaises(PlanningError):self.cmd.allocate(self.pid,'income_source',1,'1')
        with self.assertRaises(PlanningError):SqlitePlanningRepository._column('bad')
        short=self.s['positions'].save(Position(self.aid,name='Short',quantity=-2,price=10))
        with self.assertRaises(PlanningError):self.cmd.allocate(self.pid,'holding',short,'1')
        self.cmd.allocate(self.pid,'holding',self.hid,'100')
        rid=self.reports.detail(self.pid)['portfolio']['rows'][0]['id']
        other=self.cmd.save_portfolio(Portfolio('Other',self.gid))
        with self.assertRaises(EntityNotFound):self.cmd.remove_allocation(other,rid)
        with self.assertRaises(EntityNotFound):self.cmd.remove_allocation(self.pid,999)

    def test_database_enforces_total_shares_and_foreign_keys(self):
        self.cmd.allocate(self.pid,'holding',self.hid,'60')
        other=self.cmd.save_portfolio(Portfolio('Other',self.gid))
        self.cmd.allocate(other,'holding',self.hid,'40')
        con=self.vault.con
        invalid=[('UPDATE allocations SET share_bps=6000 WHERE portfolio_id=?',(other,)),
                 ('INSERT INTO allocations(portfolio_id,holding_id,share_bps) VALUES (?,?,1)',(other,self.hid)),
                 ('INSERT INTO allocations(portfolio_id,asset_id,share_bps) VALUES (?,?,0)',(other,self.asset)),
                 ('INSERT INTO allocations(portfolio_id,share_bps) VALUES (?,1)',(other,)),
                 ('INSERT INTO allocations(portfolio_id,asset_id,holding_id,share_bps) VALUES (?,?,?,1)',(other,self.asset,self.hid)),
                 ('DELETE FROM goals WHERE id=?',(self.gid,)),('DELETE FROM holdings WHERE id=?',(self.hid,)),
                 ('INSERT INTO portfolios(name,goal_id) VALUES (?,999)',('Bad',))]
        for sql,args in invalid:
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):con.execute(sql,args)
            con.rollback()

    def test_each_mutation_rolls_back_memory_and_ciphertext_on_disk_failure(self):
        self.cmd.allocate(self.pid,'holding',self.hid,'50')
        rid=self.reports.detail(self.pid)['portfolio']['rows'][0]['id']
        unattached=self.cmd.save_goal(Goal('Spare',Decimal('1')))
        actions=[lambda:self.cmd.save_goal(Goal('New',Decimal('1'))),lambda:self.cmd.save_portfolio(Portfolio('New',self.gid)),lambda:self.cmd.allocate(self.pid,'holding',self.hid,'100'),lambda:self.cmd.remove_allocation(self.pid,rid),lambda:self.cmd.delete_portfolio(self.pid),lambda:self.cmd.delete_goal(unattached)]
        for action in actions:
            before=self.dump();encrypted=Path(self.vault.path).read_bytes()
            with patch('vault.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):action()
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)
            self.assertFalse(self.vault.dirty())

    def test_sql_dump_roundtrip_keeps_allocations_and_triggers(self):
        self.cmd.allocate(self.pid,'holding',self.hid,'100')
        self.cmd.allocate(self.pid,'asset',self.asset,'100')
        expected=self.reports.overview()
        connection=self.vault.con
        class DumpOnlyConnection:
            def __getattr__(self,name):
                if name=='serialize':raise AttributeError(name)
                return getattr(connection,name)
        self.vault.con=DumpOnlyConnection()
        self.vault.save(force=True);self.vault.lock();self.vault.unlock('password123')
        db.init_db(self.vault.con)
        self.assertEqual(self.reports.overview(),expected)
        self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(),[])
        other=self.cmd.save_portfolio(Portfolio('Other',self.gid))
        with self.assertRaises(sqlite3.IntegrityError):
            self.vault.con.execute('INSERT INTO allocations(portfolio_id,asset_id,share_bps) VALUES (?,?,1)',(other,self.asset))
        self.vault.con.rollback()

    def test_populated_pages_handle_missing_fx_and_archived_allocations(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as session:session['vault']=self.vault.token
        self.cmd.save_goal(Goal('FX',Decimal('10'),currency='ZZZ',id=self.gid))
        self.cmd.allocate(self.pid,'asset',self.asset,'100')
        for lang,message in [('ru','Недостаточно данных для полной оценки'),('en','Insufficient data for a complete valuation')]:
            self.s['change_language'].execute(lang)
            for url in ('/settings','/portfolios',f'/portfolios/{self.pid}'):
                response=client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertIn(message,response.get_data(as_text=True))
        self.s['accounts'].toggle_archived(self.asset)
        self.assertIn('Archived asset',client.get(f'/portfolios/{self.pid}').get_data(as_text=True))

    def test_http_crud_localization_read_only_and_scope(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as session:session['vault']=self.vault.token
        self.assertEqual(client.post('/goals/save',data={'name':'<script>goal</script>','target_amount':'200','currency':'rub'}).status_code,302)
        response=client.post('/portfolios/save',data={'name':'<script>portfolio</script>','goal_id':self.gid})
        self.assertEqual(response.status_code,302)
        other=int(response.location.rsplit('/',1)[1])
        for lang in ('ru','en'):
            client.post('/settings/general',data={'language':lang})
            before=self.dump()
            for url in ('/settings','/portfolios',f'/portfolios/{self.pid}',f'/portfolios/{other}'):
                page=client.get(url);self.assertEqual(page.status_code,200)
                self.assertNotIn('<script>portfolio</script>',page.get_data(as_text=True))
            self.assertEqual(self.dump(),before)
        self.assertIn('Goals',client.get('/settings').get_data(as_text=True))
        self.assertEqual(client.post(f'/portfolios/{self.pid}/allocate',data={'source':f'holding:{self.hid}','percent':'100'}).status_code,302)
        page=client.post(f'/portfolios/{other}/allocate',data={'source':f'holding:{self.hid}','percent':'1'})
        self.assertEqual(page.status_code,400);self.assertIn('exceeds 100 percent',page.get_data(as_text=True))
        rid=self.reports.detail(self.pid)['portfolio']['rows'][0]['id']
        self.assertEqual(client.post(f'/portfolios/{other}/allocations/{rid}/delete').status_code,404)
        self.assertEqual(client.post(f'/portfolios/{self.pid}/allocations/{rid}/delete').status_code,302)
        self.assertEqual(client.post(f'/portfolios/{other}/delete').status_code,302)
        spare=self.cmd.save_goal(Goal('Spare',Decimal('1')))
        self.assertEqual(client.post(f'/goals/{spare}/delete').status_code,302)
        self.assertEqual(client.get('/portfolios/999').status_code,404)
        for url,data in [('/goals/save',{'name':'X','target_amount':'bad'}),('/portfolios/save',{'name':'X'}),(f'/portfolios/{self.pid}/allocate',{'source':'bad'})]:
            self.assertEqual(client.post(url,data=data).status_code,400)
        client.post('/lock');self.assertEqual(client.get('/portfolios').status_code,302)
        with self.assertRaises(RuntimeError):SqlitePlanningReader(self.vault).read()


class PlanningMigrationTests(unittest.TestCase):
    def test_v4_upgrade_is_additive_idempotent_and_atomic(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        db._init_legacy(con);db._migrate_entities(con)
        before=list(con.iterdump())
        with patch('db.PLANNING_SCHEMA',db.PLANNING_SCHEMA+' INVALID SQL;'):
            with self.assertRaises(sqlite3.OperationalError):db.init_db(con)
        self.assertEqual(before,list(con.iterdump()))
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],4)
        db.init_db(con)
        for table in ('goals','portfolios','allocations'):self.assertEqual(con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        after=list(con.iterdump());db.init_db(con);self.assertEqual(after,list(con.iterdump()))
        self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
