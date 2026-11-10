"""Upgrade real v3 rows, not a database already created by the new schema."""
import sqlite3
import tempfile
from pathlib import Path
from decimal import Decimal
from datetime import date
from dataclasses import replace
import unittest
from unittest.mock import patch
import db
from vault import Vault
from wealth_manager.bootstrap import build_services
from wealth_manager.factory import create_app
from wealth_manager.adapters.persistence.migrations import initialize_vault
from wealth_manager.adapters.persistence.reports import read_connection
from wealth_manager.modules.reporting.domain import portfolio, history, income_report
from wealth_manager.modules.reporting.application import rates_for
from wealth_manager.modules.accounts.domain import Account, Purchase
from wealth_manager.modules.assets.domain import Asset
from wealth_manager.modules.income_sources.domain import IncomeSource
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.rentals.domain import Lease, RentPayment
from wealth_manager.shared.errors import EntityNotFound


def v3(con):
    db._init_legacy(con)
    for row in [(10,'Broker','broker',0,0,0),(20,'Home','realty',1200,0,0),(30,'Client','freelance',0,6,0),(40,'Archived','crypto',0,0,1)]:
        con.execute('INSERT INTO accounts(id,name,kind,current_value,tax_rate,archived,created_at) VALUES (?,?,?,?,?,?,?)',row+('2020-01-01',))
    con.execute("INSERT INTO holdings(id,account_id,name,ticker,quantity,avg_price,price) VALUES (11,10,'Stock','SBER',10,10,12)")
    con.execute("INSERT INTO leases(id,account_id,tenant,start_date,end_date,rent_amount) VALUES (21,20,'Tenant','2026-01-01','2026-12-31',100)")
    con.execute("INSERT INTO rent_payments(id,lease_id,period_start,period_end,due_date,amount,currency,status,received_date,received_amount,received_currency) VALUES (22,21,'2026-01-01','2026-01-31','2026-01-01',100,'RUB','received','2026-01-05',100,'RUB')")
    for row in [(1,10,'deposit',100,None),(2,20,'deposit',1000,None),(3,20,'income',100,22),(4,30,'income',500,None),(5,40,'deposit',200,None)]:
        con.execute('INSERT INTO cashflows(id,account_id,type,amount,rent_payment_id,date) VALUES (?,?,?,?,?,?)',row+('2026-01-05',))
    con.execute("INSERT INTO snapshots VALUES ('2026-01-05',20,1200,1000)")
    con.commit()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.vault=Vault(str(Path(self.tmp.name)/'test.enc'));self.vault.create('password123')
        self.addCleanup(self.vault.lock);v3(self.vault.con);self.vault.save(force=True)
    def rows(self,table):return [dict(r) for r in self.vault.con.execute('SELECT * FROM '+table)]
    def summaries(self):
        data=read_connection(self.vault.con);rates=rates_for(data);today=date(2026,11,10)
        return portfolio(data,rates,today,True),history(data,rates),income_report(data,rates,today)

    def test_upgrade_preserves_every_link_and_financial_result(self):
        original={t:self.rows(t) for t in ('accounts','holdings','cashflows','leases','rent_payments','snapshots','rates','settings')}
        expected=self.summaries()
        initialize_vault(self.vault)
        self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],4)
        self.assertEqual(self.rows('accounts'),original['accounts'])
        for table in ('holdings','cashflows','rent_payments','snapshots','rates','settings'):
            self.assertEqual(self.rows(table),original[table],table)
        self.assertEqual(self.rows('leases'),[dict((('asset_id' if k=='account_id' else k),v) for k,v in row.items()) for row in original['leases']])
        self.assertEqual(self.summaries(),expected)
        self.assertEqual([r['id'] for r in self.rows('financial_accounts')],[10,40])
        self.assertEqual([r['id'] for r in self.rows('assets')],[20]);self.assertEqual([r['id'] for r in self.rows('income_sources')],[30])
        self.assertEqual(len(self.rows('legacy_entity_map')),4)
        self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(),[])
        before=list(self.vault.con.iterdump());initialize_vault(self.vault)
        self.assertEqual(list(self.vault.con.iterdump()),before)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.summaries(),expected)

    def test_disk_failure_restores_v3_in_memory_and_on_disk(self):
        before=list(self.vault.con.iterdump());encrypted=Path(self.vault.path).read_bytes()
        with patch('vault.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):initialize_vault(self.vault)
        self.assertEqual(list(self.vault.con.iterdump()),before)
        self.assertFalse(self.vault.dirty());self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)
        self.vault.lock();self.vault.unlock('password123');self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],3)

    def test_sql_dump_fallback_preserves_schema_version_and_reopens(self):
        initialize_vault(self.vault)
        connection=self.vault.con
        class DumpOnlyConnection:
            def __getattr__(self,name):
                if name=='serialize':raise AttributeError(name)
                return getattr(connection,name)
        self.vault.con=DumpOnlyConnection()
        self.vault.save(force=True)
        self.vault.lock();self.vault.unlock('password123')
        self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],4)
        before=self.summaries();db.init_db(self.vault.con)
        self.assertEqual(self.summaries(),before)

    def test_failed_unlock_migration_cannot_be_bypassed_on_retry(self):
        encrypted=Path(self.vault.path).read_bytes()
        self.vault.lock()
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with patch('vault.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):client.post('/unlock',data={'password':'password123'})
        self.assertFalse(self.vault.unlocked)
        self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)
        self.assertEqual(client.post('/unlock',data={'password':'password123'}).status_code,302)
        self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],4)
        self.assertEqual(client.get('/assets/20').status_code,200)

    def test_schema_failure_rolls_back_and_can_be_retried(self):
        before=list(self.vault.con.iterdump())
        with patch('db.ENTITY_SCHEMA',db.ENTITY_SCHEMA+' INVALID SQL;'):
            with self.assertRaises(sqlite3.OperationalError):db.init_db(self.vault.con)
        self.assertEqual(list(self.vault.con.iterdump()),before)
        db.init_db(self.vault.con);self.assertEqual(len(self.rows('assets')),1)

    def test_invalid_legacy_links_and_unknown_kinds_fail_without_loss(self):
        for sql in ("UPDATE leases SET account_id=10","UPDATE accounts SET kind='unknown' WHERE id=10"):
            with self.subTest(sql=sql):
                backup=sqlite3.connect(':memory:');self.vault.con.backup(backup)
                self.vault.con.execute(sql);self.vault.con.commit();before=list(self.vault.con.iterdump())
                with self.assertRaises(ValueError):db.init_db(self.vault.con)
                self.assertEqual(list(self.vault.con.iterdump()),before)
                backup.backup(self.vault.con);backup.close()

    def test_corrupt_fk_future_version_and_active_transaction_are_rejected(self):
        con=self.vault.con
        con.execute('PRAGMA foreign_keys=OFF');con.execute("INSERT INTO holdings(account_id,name) VALUES (999,'orphan')");con.commit()
        before=list(con.iterdump())
        with self.assertRaises(ValueError):db.init_db(con)
        self.assertEqual(list(con.iterdump()),before)
        con.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):db.init_db(con)
        con.execute('PRAGMA user_version=3');con.execute("UPDATE settings SET value=value")
        with self.assertRaises(RuntimeError):db.init_db(con)
        con.rollback()

    def test_older_schema_is_upgraded_in_one_restorable_operation(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        c.executescript("CREATE TABLE accounts(id INTEGER PRIMARY KEY,name TEXT,kind TEXT CHECK(kind IN ('broker','realty')),institution TEXT,currency TEXT,current_value REAL,note TEXT,archived INTEGER,created_at TEXT); INSERT INTO accounts VALUES (7,'Old','realty','','RUB',100,'',0,'2020-01-01'); PRAGMA user_version=1;")
        db.init_db(c)
        self.assertEqual(c.execute('SELECT current_value FROM assets WHERE id=7').fetchone()[0],100)
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_typed_workflows_after_upgrade_delete_without_orphans_or_id_reuse(self):
        initialize_vault(self.vault);s=build_services(self.vault)
        aid=s['save_asset'].execute(Asset('New home',current_value=Decimal('250')),Purchase(Decimal('200'),date(2020,1,1)))
        self.assertGreater(aid,40)
        iid=s['save_income_source'].execute(IncomeSource('New client',tax_rate=Decimal('6')))
        fid=s['save_financial_account'].execute(Account('New broker'))
        self.assertEqual(len({aid,iid,fid}),3)
        s['accounts'].delete(20)
        for table in ('leases','rent_payments'):self.assertEqual(self.rows(table),[])
        self.assertFalse(any(r['account_id']==20 for r in self.rows('cashflows')))
        self.assertFalse(any(r['id']==20 for r in self.rows('legacy_accounts_v3')))
        self.assertFalse(any(r['legacy_account_id']==20 for r in self.rows('legacy_entity_map')))
        self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(),[])
        with self.assertRaises(sqlite3.IntegrityError):self.vault.con.execute('INSERT INTO assets(id) VALUES (?)',(fid,))
        self.vault.con.rollback()

    def test_entity_type_changes_and_wrong_owners_are_rejected(self):
        initialize_vault(self.vault);s=build_services(self.vault)
        actions=[lambda:s['save_asset'].execute(Asset('Wrong',id=10)),lambda:s['save_income_source'].execute(IncomeSource('Wrong',id=20)),lambda:s['positions'].save(Position(20)),lambda:s['rentals'].save_lease(Lease(10,'Wrong','2026-01-01')),lambda:s['accounts'].set_value(10,50)]
        for action in actions:
            before=list(self.vault.con.iterdump())
            with self.assertRaises(ValueError):action()
            self.assertEqual(list(self.vault.con.iterdump()),before)
        for bad in (Account('X',kind='realty'),Asset('',current_value=Decimal('1')),Asset('X',current_value=Decimal('NaN')),IncomeSource('X',tax_rate=Decimal('101'))):
            with self.assertRaises(ValueError):bad.validate()

    def test_each_new_use_case_rolls_back_on_disk_failure(self):
        initialize_vault(self.vault);s=build_services(self.vault)
        for service,entity in [('save_asset',Asset('X')),('save_income_source',IncomeSource('X')),('save_financial_account',Account('X'))]:
            before=list(self.vault.con.iterdump())
            with patch('vault.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):s[service].execute(entity)
            self.assertEqual(list(self.vault.con.iterdump()),before)

    def test_canonical_forms_and_urls_keep_localization_and_legacy_links(self):
        initialize_vault(self.vault)
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as session:session['vault']=self.vault.token
        for url,data in [('/assets/save',{'name':'Дом','current_value':'150','purchase_price':'100'}),('/income-sources/save',{'name':'Клиент','tax_rate':'6'})]:
            r=client.post(url,data=data);self.assertEqual(r.status_code,302);self.assertEqual(client.get(r.location).status_code,200)
        self.assertEqual(client.post('/assets/save',data={'id':10,'name':'Wrong'}).status_code,400)
        self.assertEqual(client.post('/income-sources/save',data={'name':'Bad','tax_rate':101}).status_code,400)
        self.assertEqual(client.get('/assets/10').status_code,404)
        self.assertEqual(client.get('/assets/999').status_code,404)
        for lang in ('ru','en'):
            client.post('/settings/general',data={'language':lang})
            for url in ('/assets/20','/income-sources/30','/accounts/20','/accounts/30','/'):
                self.assertEqual(client.get(url).status_code,200,url)
            page=client.get('/').get_data(as_text=True)
            self.assertIn('/assets/20',page);self.assertIn('/income-sources/30',page)
