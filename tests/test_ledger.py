"""Acceptance tests for opening balances, atomic trades/transfers and replay."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_valuation_quality
from wealth_manager.modules.ledger.domain import Entry, replay, validate_links
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.accounts.domain import Account
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound

class LedgerTests(unittest.TestCase):
    setUp=test_valuation_quality.ValuationIntegrationTests.setUp
    dump=test_valuation_quality.ValuationIntegrationTests.dump
    def prepare(self,amount=1000):
        self.s['positions'].save(Position(self.aid,id=self.hid,name='Stock',ticker='ABC',quantity=0,price=20))
        self.cash=self.s['positions'].save(Position(self.aid,name='Cash',asset_class_code='cash',quantity=amount))
        self.cmd=self.s['ledger'];self.cmd.activate(self.aid)
    def row(self,hid):return dict(self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(hid,)).fetchone())
    def entry(self,kind='buy',**kw):
        data=dict(date=self.today.isoformat(),kind=kind,holding_id=self.hid,quantity=D('10'),price=D('20'),fee=D('2'),cash_id=self.cash,request_id=kind)
        data.update(kw);return Entry(**data)
    def target(self):
        aid=self.s['save_financial_account'].execute(Account('Destination'))
        cash=self.s['positions'].save(Position(aid,name='Cash',asset_class_code='cash'))
        stock=self.s['positions'].save(Position(aid,name='Stock',ticker='ABC',price=20))
        self.cmd.activate(aid);return aid,cash,stock
    def test_activation_preserves_capital_cost_and_does_not_replay_legacy_flows(self):
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',10))
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'withdrawal',5))
        before=self.s['reports'].dashboard()['total'];n=self.vault.con.execute('SELECT COUNT(*) FROM cashflows').fetchone()[0]
        self.s['ledger'].activate(self.aid);self.s['ledger'].activate(self.aid)
        after=self.s['reports'].dashboard()['total']
        self.assertEqual((before['value'],before['invested'],before['profit']),(after['value'],after['invested'],after['profit']))
        self.assertEqual(self.vault.con.execute('SELECT COUNT(*) FROM cashflows').fetchone()[0],n)
        self.assertEqual(self.row(self.hid)['quantity'],2)
    def test_buy_sell_fee_basis_realized_result_and_cancel(self):
        self.prepare()
        buy=self.cmd.post(self.entry());self.assertEqual(self.row(self.cash)['quantity'],798)
        self.assertEqual(self.row(self.hid)['avg_price'],D('20.2'))
        sale=self.cmd.post(self.entry('sell',quantity=D(4),price=D(30),fee=D(1)))
        self.assertEqual(self.row(self.cash)['quantity'],917)
        self.assertEqual(self.row(self.hid)['quantity'],6)
        self.assertEqual(self.vault.con.execute('SELECT realized FROM ledger_entries WHERE id=?',(sale,)).fetchone()[0],D('38.2'))
        self.assertEqual(self.s['reports'].dashboard()['total']['profit'],37)
        self.cmd.cancel(sale);self.cmd.cancel(sale)
        self.assertEqual(self.row(self.hid)['quantity'],10)
        self.cmd.cancel(buy)
        self.assertEqual(self.row(self.cash)['quantity'],1000)
        self.assertEqual(self.row(self.hid)['quantity'],0)
        self.assertEqual(self.vault.con.execute('SELECT COUNT(*) FROM cashflows').fetchone()[0],0)
    def test_transfer_cash_and_assets_carries_basis_without_double_capital(self):
        self.prepare();aid,cash,stock=self.target()
        self.cmd.post(self.entry())
        before=self.s['reports'].dashboard()['total']['value']
        self.cmd.post(self.entry('transfer',quantity=5,price=30,fee=1,target_id=stock))
        self.assertEqual(self.row(stock)['quantity'],5);self.assertEqual(self.row(stock)['avg_price'],D('20.2'))
        self.assertEqual(self.s['reports'].dashboard()['total']['value'],before-1)
        self.cmd.post(self.entry('transfer',holding_id=self.cash,quantity=D('0.1'),target_id=cash,fee=0,request_id='cash-transfer'))
        self.assertEqual(self.row(cash)['quantity'],D('0.1'))
        self.assertEqual(self.s['reports'].dashboard()['total']['invested'],1000)
    def test_deposit_income_withdrawal_expense_and_idempotency(self):
        self.prepare(0)
        for kind,quantity in [('deposit',100),('income',D('0.3')),('withdrawal',20),('expense',D('0.1'))]:
            e=self.entry(kind,holding_id=self.cash,quantity=quantity,cash_id=None,price=0,fee=0)
            eid=self.cmd.post(e);self.assertEqual(self.cmd.post(e),eid)
            with self.assertRaises(RecordError):self.cmd.post(replace(e,quantity=D('1')))
        self.assertEqual(self.row(self.cash)['quantity'],D('80.2'))
        totals=self.s['reports'].operations()['totals'];self.assertEqual(totals['income'],D('0.3'))
        self.assertEqual(totals['expense'],D('0.1'))
    def test_insufficient_funds_and_dependent_cancellation_roll_back(self):
        self.prepare(0)
        deposit=self.cmd.post(self.entry('deposit',holding_id=self.cash,quantity=100,fee=0,price=0,cash_id=None))
        self.cmd.post(self.entry(quantity=4,fee=0))
        for action in (lambda:self.cmd.cancel(deposit),lambda:self.cmd.post(self.entry('sell',quantity=5)),lambda:self.cmd.post(self.entry(request_id='too-expensive'))):
            before=self.dump();disk=Path(self.vault.path).read_bytes()
            with self.assertRaises(RecordError):action()
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
    def test_ledger_blocks_manual_balance_flow_and_deletion_but_allows_quotes(self):
        self.prepare();self.cmd.post(self.entry())
        fid=self.vault.con.execute('SELECT id FROM cashflows LIMIT 1').fetchone()[0]
        for action in (lambda:self.s['positions'].save(Position(self.aid,id=self.hid,quantity=1)),lambda:self.s['positions'].delete(self.hid),lambda:self.s['positions'].save(Position(self.aid,quantity=1,price=1)),lambda:self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',1)),lambda:self.s['cashflows'].delete(fid),lambda:self.s['cashflows'].mark_received(fid),lambda:self.s['accounts'].delete(self.aid)):
            with self.assertRaises(RecordError):action()
        new=self.s['positions'].save(Position(self.aid,name='New',price=5))
        self.s['positions'].save(Position(self.aid,id=new,name='Renamed',price=5))
        self.s['positions'].update_prices(self.aid,{self.hid:D('21.123456789')})
        self.assertEqual(self.row(self.hid)['quantity'],10);self.assertEqual(self.row(self.hid)['price'],D('21.123456789'))
    def test_validation_bad_dates_links_fees_and_short_positions(self):
        self.prepare();aid,cash,stock=self.target()
        variants=[dict(kind='bad'),dict(quantity=0),dict(fee=-1),dict(request_id=''),dict(date='bad'),dict(date=(self.today+timedelta(days=1)).isoformat()),dict(date=(self.today-timedelta(days=1)).isoformat()),dict(holding_id=999),dict(cash_id=stock),dict(cash_id=cash),dict(cash_id=None),dict(holding_id=self.cash),dict(price=0),dict(target_id=stock),dict(kind='transfer',target_id=self.hid),dict(kind='transfer',target_id=stock,cash_id=None),dict(kind='income'),dict(kind='sell',quantity=1,fee=1000)]
        for i,v in enumerate(variants):
            with self.subTest(v=v),self.assertRaises((ValueError,RecordError)):self.cmd.post(self.entry(**v))
        with self.assertRaises(EntityNotFound):self.cmd.cancel(999)
        third=self.s['save_financial_account'].execute(Account('Unmanaged'))
        h=self.s['positions'].save(Position(third,quantity=-1))
        with self.assertRaises(RecordError):self.cmd.activate(third)
        self.s['positions'].save(Position(third,id=h,quantity=0))
        fid=self.s['cashflows'].save(Cashflow(third,self.day,'income',1,pending=True))
        with self.assertRaises(RecordError):self.cmd.activate(third)
        self.s['cashflows'].delete(fid)
        self.s['accounts'].close(third)
        with self.assertRaises(RecordError):self.cmd.activate(third)
    def test_post_cancel_and_activation_rollback_on_disk_failure(self):
        self.prepare()
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        with patch('vault.os.replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.cmd.post(self.entry())
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        eid=self.cmd.post(self.entry());before=self.dump()
        with patch('vault.os.replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.cmd.cancel(eid)
        self.assertEqual(self.dump(),before)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.row(self.hid)['quantity'],10)
    def test_http_locales_read_only_and_repeated_submission(self):
        self.prepare()
        for lang,term in [('ru','Журнал сделок'),('en','Trades and balances')]:
            self.s['change_language'].execute(lang);before=self.dump();disk=Path(self.vault.path).read_bytes()
            for url in ('/ledger','/operations',f'/accounts/{self.aid}'):
                r=self.client.get(url);self.assertEqual(r.status_code,200);self.assertIn(term,r.get_data(as_text=True))
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        data=dict(date=self.day,kind='buy',holding_id=self.hid,quantity='0.1',price='20',fee='0.01',cash_id=self.cash,request_id='http',note='<script>alert(1)</script>')
        for _ in range(2):self.assertEqual(self.client.post('/ledger/post',data=data).status_code,302)
        self.assertEqual(self.row(self.hid)['quantity'],D('0.1'))
        self.assertNotIn('<script>alert(1)</script>',self.client.get('/ledger').get_data(as_text=True))
        eid=self.vault.con.execute('SELECT id FROM ledger_entries').fetchone()[0]
        self.assertEqual(self.client.post(f'/ledger/{eid}/cancel').status_code,302)
        self.assertEqual(self.client.post('/ledger/activate/999').status_code,404)
        self.assertEqual(self.client.post('/ledger/999/cancel').status_code,404)
    def test_backdated_correction_marks_history_incomplete_until_revalued(self):
        self.prepare()
        self.today+=timedelta(days=1)
        eid=self.cmd.post(self.entry())
        historical_day=self.today.isoformat()
        self.today+=timedelta(days=1)
        self.cmd.cancel(eid)
        hist=self.s['reports'].dashboard()['hist']
        self.assertIsNone(next(r['value'] for r in hist if r['date']==historical_day))
        self.s['accounts'].record_history(self.aid,historical_day,1000)
        self.assertEqual(next(r['value'] for r in self.s['reports'].dashboard()['hist'] if r['date']==historical_day),1000)

class LedgerMigrationTests(unittest.TestCase):
    def test_v10_no_balances_reapplied_migration_rollback_and_idempotency(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        with patch('db._migrate_ledger'),patch('db._migrate_liabilities'),patch('db._migrate_tax_documents'),patch('db._migrate_goal_forecasts'),patch('db._migrate_instruments'),patch('db._migrate_macro_versions'),patch('db._migrate_macro_calculations'):db.init_db(c)
        c.execute("INSERT INTO entities(id,entity_type,name) VALUES (1,'account','Old')")
        c.execute("INSERT INTO financial_accounts(id,kind) VALUES (1,'broker')")
        c.execute("INSERT INTO holdings(account_id,name,quantity,price) VALUES (1,'Stock','0.123456789012345678','10')");c.commit()
        before=list(c.iterdump());original=db._migrate_ledger
        def fail(con):original(con);raise RuntimeError('migration')
        with patch('db._migrate_ledger',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(c)
        self.assertEqual(list(c.iterdump()),before)
        db.init_db(c);self.assertEqual(c.execute('SELECT quantity FROM holdings').fetchone()[0],D('0.123456789012345678'))
        self.assertEqual(c.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0],0)
        before=list(c.iterdump());db.init_db(c);self.assertEqual(list(c.iterdump()),before)
