"""Debt service, property cash income and net worth acceptance tests."""
from dataclasses import replace,asdict
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_ledger
from wealth_manager.modules.liabilities.domain import Liability,Payment
from wealth_manager.modules.assets.domain import Asset
from wealth_manager.modules.accounts.domain import Account
from wealth_manager.modules.income_sources.domain import IncomeSource
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.rentals.domain import Lease,RentPayment
from wealth_manager.shared.records import RecordError

class LiabilityTests(unittest.TestCase):
    setUp=test_ledger.LedgerTests.setUp
    dump=test_ledger.LedgerTests.dump
    prepare=test_ledger.LedgerTests.prepare
    row=test_ledger.LedgerTests.row
    def debt(self,**kw):
        data=dict(name='Mortgage',opening_date=self.day,opening_principal=D(500),request_id='debt')
        data.update(kw);return Liability(**data)
    def payment(self,lid,**kw):
        data=dict(liability_id=lid,date=self.day,principal=D(100),interest=D(10),fee=D(1),request_id='payment')
        data.update(kw);return Payment(**data)
    def test_net_worth_principal_interest_cash_link_and_protected_cancel(self):
        self.prepare(1000);cmd=self.s['liabilities'];debt=self.debt();lid=cmd.create(debt)
        self.assertEqual(cmd.create(debt),lid)
        self.assertEqual(self.s['reports'].dashboard()['total']['net_worth'],500)
        payment=self.payment(lid,cash_id=self.cash);pid=cmd.pay(payment)
        self.assertEqual(cmd.pay(payment),pid)
        self.assertEqual(self.row(self.cash)['quantity'],889)
        total=self.s['reports'].dashboard()['total'];self.assertEqual(total['liabilities'],400);self.assertEqual(total['net_worth'],489)
        income=self.s['reports'].income()['inc'];self.assertEqual(income['totals']['expense'],11)
        self.assertEqual(income['totals']['net'],-11)
        eids=[r[0] for r in self.vault.con.execute('SELECT entry_id FROM liability_entries')]
        for eid in eids:
            with self.assertRaises(RecordError):self.s['ledger'].cancel(eid)
        with self.assertRaises(RecordError):cmd.cancel(lid)
        cmd.cancel_payment(pid);cmd.cancel_payment(pid)
        self.assertEqual(self.row(self.cash)['quantity'],1000)
        self.assertEqual(self.s['reports'].liabilities()['debts']['total'],500)
        with self.assertRaises(RecordError):cmd.pay(payment)
        cmd.cancel(lid);cmd.cancel(lid)
        self.assertEqual(self.s['reports'].dashboard()['total']['net_worth'],1000)
        with self.assertRaises(RecordError):cmd.create(debt)

    def test_property_equity_cash_income_external_payments_and_deposits(self):
        asset=self.s['save_asset'].execute(Asset('Flat',current_value=D(1000)))
        lid=self.s['liabilities'].create(self.debt(asset_id=asset,kind='mortgage'))
        deposit=self.s['liabilities'].create(self.debt(asset_id=asset,kind='deposit',opening_principal=D(50),request_id='deposit'))
        self.s['cashflows'].save(Cashflow(asset,self.day,'income',D(100),category_code='rent'))
        self.s['cashflows'].save(Cashflow(asset,self.day,'expense',D(20),category_code='utilities'))
        self.s['cashflows'].save(Cashflow(asset,self.day,'income',D(30),pending=True))
        self.s['liabilities'].pay(self.payment(lid))
        cash=self.s['reports'].account(asset)[1]['rent']['cash']
        self.assertEqual(cash['net'],69);self.assertEqual(cash['after_principal'],-31)
        self.assertEqual(cash['yield_pct'],D('0.069'));self.assertEqual(cash['equity'],550)
        self.assertEqual(self.s['reports'].income()['inc']['totals']['expense'],31)
        self.assertEqual(self.s['reports'].income()['inc']['totals']['income'],100)
        self.assertEqual(self.s['reports'].account(self.aid)[1]['m']['expense'],0)
        with self.assertRaises(RecordError):self.s['accounts'].delete(asset)
        self.s['accounts'].toggle_archived(asset)
        self.assertEqual(self.s['reports'].liabilities()['debts']['total'],450)
        self.s['liabilities'].pay(self.payment(deposit,principal=D(50),interest=D(0),fee=D(0),request_id='refund'))
        self.assertEqual(self.s['reports'].income()['inc']['totals']['income'],100)
        self.assertEqual(self.s['reports'].liabilities()['debts']['total'],400)

    def test_unknown_currency_makes_net_worth_and_property_equity_unknown(self):
        asset=self.s['save_asset'].execute(Asset('Flat',current_value=D(1000)))
        lid=self.s['liabilities'].create(self.debt(currency='ZZZ',asset_id=asset))
        self.assertIsNone(self.s['reports'].dashboard()['total']['net_worth'])
        self.assertIn('ZZZ',self.s['reports'].liabilities()['debts']['missing'])
        self.assertIsNone(self.s['reports'].account(asset)[1]['rent']['cash']['equity'])
        self.s['liabilities'].pay(self.payment(lid))
        self.assertIsNone(self.s['reports'].income()['inc']['totals']['net'])
        self.assertIsNone(self.s['reports'].account(asset)[1]['rent']['cash']['net'])

    def test_invalid_values_overpayment_repeated_id_and_bad_cash_do_not_mutate(self):
        self.prepare();cmd=self.s['liabilities'];lid=cmd.create(self.debt())
        for change in (dict(opening_principal=D(-1)),dict(name=''),dict(kind='bad'),dict(opening_date='bad'),dict(opening_date=(self.today+timedelta(days=1)).isoformat()),dict(due_date='1900-01-01'),dict(kind='deposit'),dict(request_id='')):
            with self.subTest(change=change),self.assertRaises((RecordError,ValueError)):cmd.create(self.debt(**change))
        for change in (dict(principal=D(-1)),dict(principal=D(501)),dict(principal=D(0),interest=D(0),fee=D(0)),dict(date='1900-01-01'),dict(date=(self.today+timedelta(days=1)).isoformat()),dict(request_id=''),dict(cash_id=self.hid),dict(cash_id=999)):
            before=self.dump()
            with self.subTest(change=change),self.assertRaises((RecordError,ValueError)):cmd.pay(self.payment(lid,**change))
            self.assertEqual(self.dump(),before)
        with self.assertRaises(RecordError):cmd.create(self.debt(name='Other'))
        cmd.pay(self.payment(lid))
        with self.assertRaises(RecordError):cmd.pay(self.payment(lid,interest=D(20)))

    def test_insufficient_cash_disk_failure_and_closed_account_rollback(self):
        self.prepare(0);cmd=self.s['liabilities'];lid=cmd.create(self.debt())
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        with self.assertRaises(RecordError):cmd.pay(self.payment(lid,cash_id=self.cash))
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        with patch('vault.os.replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):cmd.pay(self.payment(lid))
        self.assertEqual(self.dump(),before)
        self.s['ledger'].post(test_ledger.LedgerTests.entry(self,'deposit',holding_id=self.cash,quantity=111,price=0,fee=0,cash_id=None))
        pid=cmd.pay(self.payment(lid,cash_id=self.cash))
        self.s['accounts'].close(self.aid)
        before=self.dump()
        with self.assertRaises(RecordError):cmd.cancel_payment(pid)
        self.assertEqual(self.dump(),before)
        self.s['accounts'].reopen(self.aid)
        with patch('vault.os.replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):cmd.cancel_payment(pid)
        self.assertEqual(self.row(self.cash)['quantity'],0)
        self.vault.lock();self.vault.unlock('password123')
        self.assertEqual(self.s['reports'].liabilities()['debts']['total'],400)

    def test_payoff_late_date_precision_and_income_periods(self):
        cmd=self.s['liabilities'];lid=cmd.create(self.debt(opening_principal=D('0.3'),due_date=self.day))
        self.today+=timedelta(days=1)
        self.assertTrue(self.s['reports'].liabilities()['debts']['rows'][0]['overdue'])
        for n in range(3):cmd.pay(self.payment(lid,principal=D('.1'),interest=D(0),fee=D(0),request_id=str(n)))
        self.assertEqual(self.s['reports'].liabilities()['debts']['total'],0)
        self.assertFalse(self.s['reports'].liabilities()['debts']['rows'][0]['overdue'])
        self.assertEqual(self.s['reports'].income(year='2020')['inc']['totals']['expense'],0)

    def test_income_received_salary_pending_and_future_are_separate(self):
        self.today=self.today.replace(day=15);self.day=self.today.isoformat()
        aid=self.s['save_income_source'].execute(IncomeSource('Employer'))
        for flow in (Cashflow(aid,self.day,'income',D(100),category_code='salary'),
                     Cashflow(aid,self.day,'expense',D(10)),
                     Cashflow(aid,self.day,'income',D(20),pending=True),
                     Cashflow(aid,(self.today+timedelta(days=1)).isoformat(),'income',D(30)),
                     Cashflow(aid,'2000-01-01','income',D(40),pending=True)):
            self.s['cashflows'].save(flow)
        income=self.s['reports'].income()['inc']['totals']
        self.assertEqual(income['income'],100);self.assertEqual(income['salary'],100)
        self.assertEqual(income['net'],90);self.assertEqual(income['pending'],50)
        past=self.s['reports'].income('2000')['inc']['totals'];self.assertEqual(past['pending'],40)
        self.s['cashflows'].save(Cashflow(self.aid,(self.today+timedelta(days=1)).isoformat(),'deposit',D(1000)))
        self.assertEqual(self.s['reports'].account(self.aid)[1]['m']['invested'],80)

    def test_rent_agent_received_and_negative_or_future_actuals(self):
        asset=self.s['save_asset'].execute(Asset('Flat',current_value=D(1000)))
        lease=Lease(asset,'Tenant',self.day,rent_amount=D(100));lid,_=self.s['rentals'].save_lease(lease)
        pid,_=self.s['rentals'].save_payment(RentPayment(lid,self.day,status='agent'))
        self.assertEqual(self.s['reports'].account(asset)[1]['rent']['cash']['received'],0)
        self.s['rentals'].save_payment(RentPayment(lid,self.day,status='received',id=pid))
        self.assertEqual(self.s['reports'].account(asset)[1]['rent']['cash']['received'],100)
        with self.assertRaises(ValueError):self.s['rentals'].save_lease(replace(lease,rent_amount=D(-1)))
        with self.assertRaises(ValueError):self.s['rentals'].save_payment(RentPayment(lid,self.day,amount=D(-1)))
        with self.assertRaises(ValueError):self.s['rentals'].save_payment(RentPayment(lid,self.day,status='received',received_date=(self.today+timedelta(days=1)).isoformat()))

    def test_debt_only_dashboard_and_interest_only_payment(self):
        self.s['accounts'].delete(self.aid)
        lid=self.s['liabilities'].create(self.debt())
        self.assertEqual(self.s['reports'].dashboard()['total']['net_worth'],-500)
        self.assertEqual(self.client.get('/').status_code,200)
        self.s['liabilities'].pay(self.payment(lid,principal=D(0),interest=D('0.1'),fee=D(0)))
        self.assertEqual(self.s['reports'].liabilities()['debts']['total'],500)
        self.assertEqual(self.s['reports'].income()['inc']['totals']['expense'],D('0.1'))

    def test_property_linked_payment_cost_is_counted_once(self):
        self.prepare()
        asset=self.s['save_asset'].execute(Asset('Flat',current_value=D(1000)))
        lid=self.s['liabilities'].create(self.debt(asset_id=asset))
        self.s['liabilities'].pay(self.payment(lid,cash_id=self.cash,principal=D(0)))
        self.assertEqual(self.s['reports'].account(asset)[1]['rent']['cash']['net'],-11)
        self.assertEqual(self.s['reports'].income()['inc']['totals']['expense'],11)
        self.assertEqual(self.s['reports'].dashboard()['total']['net_worth'],1489)

    def test_http_locales_read_only_create_pay_cancel_and_escaping(self):
        self.prepare();before=self.dump();disk=Path(self.vault.path).read_bytes()
        self.assertEqual(self.client.get('/liabilities').status_code,200)
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        data=asdict(self.debt(name='<script>x</script>'));data.pop('asset_id')
        self.assertEqual(self.client.post('/liabilities/create',data=data).status_code,302)
        lid=self.s['reports'].liabilities()['debts']['rows'][0]['id']
        payment=asdict(self.payment(lid,cash_id=self.cash))
        self.assertEqual(self.client.post('/liabilities/pay',data=payment).status_code,302)
        pid=self.s['reports'].liabilities()['payments'][0]['id']
        for lang,word in [('ru','Обязательства'),('en','Liabilities')]:
            self.s['change_language'].execute(lang)
            for path in ('/liabilities','/','/income'):
                r=self.client.get(path);self.assertEqual(r.status_code,200);self.assertIn(word,r.get_data(as_text=True));self.assertNotIn('<script>x</script>',r.get_data(as_text=True))
        self.assertEqual(self.client.post(f'/liabilities/payments/{pid}/cancel').status_code,302)
        self.assertEqual(self.client.post(f'/liabilities/{lid}/cancel').status_code,302)
        self.assertEqual(self.client.post('/liabilities/999/cancel').status_code,404)

class LiabilityMigrationTests(unittest.TestCase):
    def test_upgrade_does_not_infer_lease_deposit_or_change_old_data_and_rolls_back(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        with patch('db._migrate_liabilities'),patch('db._migrate_tax_documents'),patch('db._migrate_goal_forecasts'),patch('db._migrate_instruments'),patch('db._migrate_macro_versions'):db.init_db(c)
        self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],11)
        before=list(c.iterdump());original=db._migrate_liabilities
        def fail(con):original(con);raise RuntimeError('migration')
        with patch('db._migrate_liabilities',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(c)
        self.assertEqual(list(c.iterdump()),before)
        db.init_db(c);self.assertEqual(c.execute('SELECT COUNT(*) FROM liabilities').fetchone()[0],0)
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        before=list(c.iterdump());db.init_db(c);self.assertEqual(list(c.iterdump()),before)
