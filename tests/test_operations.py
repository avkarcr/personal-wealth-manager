"""Business workflows through the same transactional ports used by HTTP."""
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db import init_db
from vault import Vault
from wealth_manager.bootstrap import build_services
from wealth_manager.modules.entities.domain import RecordDraft as Account
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.rentals.domain import Lease, RentPayment, scheduled_payments, rent_cashflow
from wealth_manager.modules.rentals.metrics import occupancy, payment_view
from wealth_manager.modules.valuation.domain import ExchangeRates, xirr, account_metrics
from wealth_manager.shared.errors import EntityNotFound, RentalFlowProtected

TODAY = date(2024, 3, 15)

class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Vault(str(Path(self.tmp.name) / 'invest.enc'))
        init_db(self.vault.create('password123'))
        self.addCleanup(self.vault.lock)
        self.s = build_services(self.vault, today=lambda: TODAY)
        self.aid = self.s['save_account'].execute(Account('Home', kind='realty'))
        self.other = self.s['save_account'].execute(Account('Broker'))
        self.r = self.s['rentals']
        self.lease = Lease(self.aid, 'Tenant', '2024-01-31', end_date='2024-03-31', rent_amount=100, currency='USD', pay_day=31)

    def rows(self, table):
        return [dict(r) for r in self.vault.con.execute('SELECT * FROM ' + table)]

    def test_positions_and_batch_prices_cannot_modify_another_account(self):
        p = Position(self.other, ticker='sber', quantity=10, price=100)
        pid = self.s['positions'].save(p)
        self.s['positions'].save(replace(p, id=pid, price=120, avg_price=90))
        third = self.s['save_account'].execute(Account('Other broker'))
        cash = self.s['positions'].save(Position(third, asset_class='Деньги', price=5, quantity=20))
        self.s['positions'].update_prices(self.other, {pid: 130, cash: 900, 999: 10})
        rows = self.rows('holdings')
        self.assertEqual((rows[0]['name'], rows[0]['ticker'], rows[0]['avg_price'], rows[0]['price']), ('sber','SBER',90,130))
        self.assertEqual((rows[1]['price'], rows[1]['avg_price']), (1,1))
        self.assertEqual(rows[0]['updated_at'], TODAY.isoformat())
        self.s['positions'].delete(pid)
        self.assertEqual(len(self.rows('holdings')), 1)

    def test_manual_flows_and_historical_valuation(self):
        f = Cashflow(self.aid, '2024-01-01', amount=-100)
        fid = self.s['cashflows'].save(f)
        self.s['cashflows'].save(replace(f,id=fid,amount=200,pending=True))
        self.s['cashflows'].save(Cashflow(self.aid,'2024-01-02','withdrawal',50))
        self.s['cashflows'].save(Cashflow(self.aid,'2025-01-01',amount=1000))
        self.s['cashflows'].save(Cashflow(self.aid,'2024-01-02','income',10,pending=True))
        self.s['accounts'].record_history(self.aid,'2024-02-01',500)
        snap = next(r for r in self.rows('snapshots') if r['date']=='2024-02-01')
        self.assertEqual((snap['value_rub'],snap['invested_rub']),(500,150))
        self.assertFalse(self.rows('cashflows')[0]['pending'])
        self.s['cashflows'].mark_received(fid)
        self.assertEqual(self.rows('cashflows')[0]['date'],TODAY.isoformat())
        self.s['cashflows'].delete(fid)
        self.assertEqual(len(self.rows('cashflows')),3)
        self.s['market_data'].save_historical_rate('USD',90,'2024-02-01')
        self.s['accounts'].record_history(self.other,'2024-02-01',50,'USD')
        snap = next(r for r in self.rows('snapshots') if r['account_id']==self.other and r['date']=='2024-02-01')
        self.assertEqual(snap['value_rub'],snap['invested_rub'])

    def test_account_value_archive_and_delete_cascade(self):
        self.s['accounts'].set_value(self.aid,250)
        self.s['accounts'].toggle_archived(self.aid)
        self.assertEqual((self.rows('accounts')[0]['current_value'],self.rows('accounts')[0]['archived']),(250,1))
        self.s['accounts'].toggle_archived(self.aid)
        lid,_=self.r.save_lease(self.lease, True)
        self.r.save_payment(RentPayment(lid,'2024-04-01',status='received'))
        # Positions belong to financial accounts, not properties.
        self.s['accounts'].delete(self.aid)
        for table in ('leases','rent_payments','cashflows','holdings'):
            self.assertEqual(self.rows(table), [], table)

    def test_rent_status_lifecycle_owns_one_income(self):
        lid,_=self.r.save_lease(self.lease)
        p=RentPayment(lid,'2024-02-01')
        pid,aid=self.r.save_payment(p)
        self.assertEqual(aid,self.aid); self.assertEqual(self.rows('cashflows'),[])
        p=replace(p,id=pid,status='agent',tenant_paid_date='2024-02-02')
        self.r.save_payment(p)
        fid=self.rows('cashflows')[0]['id']
        self.assertEqual((self.rows('cashflows')[0]['pending'],self.rows('cashflows')[0]['amount']),(1,100))
        for action in (lambda: self.s['cashflows'].delete(fid),lambda: self.s['cashflows'].mark_received(fid),lambda: self.s['cashflows'].save(Cashflow(self.aid,'2024-02-01',id=fid))):
            with self.assertRaises(RentalFlowProtected): action()
        p=replace(p,status='received',received_amount=9500,received_currency='RUB')
        self.r.save_payment(p); self.r.save_payment(p)
        self.assertEqual(len(self.rows('cashflows')),1)
        flow=self.rows('cashflows')[0]
        self.assertEqual((flow['id'],flow['amount'],flow['pending'],flow['date']),(fid,9500,0,TODAY.isoformat()))
        new_asset=self.s['save_account'].execute(Account('Other home',kind='realty'))
        self.r.save_lease(replace(self.lease,id=lid,tenant='New tenant',asset_id=new_asset))
        self.assertEqual((self.rows('cashflows')[0]['source'],self.rows('cashflows')[0]['account_id']),('New tenant',new_asset))
        self.r.save_payment(replace(p,status='expected'))
        self.assertEqual(self.rows('cashflows'),[])
        self.r.save_payment(replace(p,status='received',received_amount=0))
        self.assertEqual(self.rows('cashflows')[0]['currency'],'USD')
        self.assertEqual(self.r.delete_payment(pid),new_asset)
        self.assertEqual(self.rows('cashflows'),[]); self.assertEqual(self.rows('rent_payments'),[])

    def test_schedule_idempotent_trim_and_delete(self):
        lid,count=self.r.save_lease(self.lease,True)
        self.assertEqual(count,3)
        self.assertEqual(self.r.generate_schedule(lid),(self.aid,0))
        rows=self.rows('rent_payments')
        self.assertEqual(rows[1]['due_date'],'2024-02-29')
        self.r.save_payment(RentPayment(lid,'2024-01-31',period_end='2024-02-28',status='received',id=rows[0]['id']))
        self.r.save_lease(replace(self.lease,id=lid,end_date='2024-02-15'))
        self.assertEqual(len(self.rows('rent_payments')),1)
        self.assertEqual(self.rows('rent_payments')[0]['period_end'],'2024-02-15')
        self.assertIn('15.02.24',self.rows('cashflows')[0]['note'])
        self.assertEqual(self.r.delete_lease(lid),self.aid)
        self.assertEqual(self.rows('rent_payments'),[]);self.assertEqual(self.rows('cashflows'),[])

    def test_missing_entities_do_not_succeed_silently(self):
        actions=[lambda:self.s['positions'].delete(999),lambda:self.s['positions'].save(Position(self.other,id=999)),lambda:self.s['cashflows'].delete(999),lambda:self.s['accounts'].set_value(999,1),lambda:self.s['accounts'].toggle_archived(999),lambda:self.s['accounts'].delete(999),lambda:self.r.delete_lease(999),lambda:self.r.delete_payment(999),lambda:self.r.generate_schedule(999)]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(EntityNotFound): action()

    def test_disk_failure_restores_every_workflow(self):
        lid,_=self.r.save_lease(self.lease)
        actions=[lambda:self.s['positions'].save(Position(self.other,price=100)),lambda:self.s['cashflows'].save(Cashflow(self.aid,'2024-01-01',amount=100)),lambda:self.s['accounts'].set_value(self.aid,123),lambda:self.s['accounts'].delete(self.aid),lambda:self.r.generate_schedule(lid),lambda:self.r.save_lease(replace(self.lease,id=lid,tenant='Changed'),True),lambda:self.r.save_payment(RentPayment(lid,'2024-02-01',status='received')),lambda:self.r.delete_lease(lid)]
        for action in actions:
            before=list(self.vault.con.iterdump()); encrypted=Path(self.vault.path).read_bytes()
            with self.subTest(action=action),patch('vault.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError): action()
            self.assertEqual(list(self.vault.con.iterdump()),before)
            self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)
            self.assertFalse(self.vault.dirty())

    def test_snapshot_failure_rolls_back_payment_and_income(self):
        lid,_=self.r.save_lease(self.lease)
        with patch('calc.take_snapshot',side_effect=RuntimeError('snapshot')):
            with self.assertRaises(RuntimeError): self.r.save_payment(RentPayment(lid,'2024-02-01',status='received'))
        self.assertEqual(self.rows('rent_payments'),[]);self.assertEqual(self.rows('cashflows'),[])

class PureDomainTests(unittest.TestCase):
    def test_validation_and_defaults(self):
        lease=Lease(1,'Tenant','2024-01-01',rent_amount=100)
        for bad in (replace(lease,frequency='bad'),replace(lease,pay_day=32),replace(lease,end_date='2023-01-01'),replace(lease,start_date='yesterday'),replace(lease,rent_amount=float('inf'))):
            with self.assertRaises(ValueError): bad.validate()
        for bad in (RentPayment(1,'2024-01-01',status='bad'),RentPayment(1,'2024-02-01',period_end='2024-01-01')):
            with self.assertRaises(ValueError):bad.normalized(lease,TODAY)
        for bad in (Cashflow(1,'bad'),Cashflow(1,'2024-01-01',type='bad'),Cashflow(1,'2024-01-01',amount=float('nan'))):
            with self.assertRaises(ValueError):bad.normalized()
        with self.assertRaises(ValueError): Position(1,price=float('inf')).normalized(TODAY)
        self.assertEqual(Position(1).normalized(TODAY).name,'Позиция')
        self.assertEqual(Cashflow(1,'2024-01-01','income',-10,'usd',pending=True).normalized().currency,'USD')
        self.assertTrue(Cashflow(1,'2024-01-01','income',pending=True).normalized().pending)
        p=RentPayment(1,'2024-01-01',status='agent').normalized(lease,TODAY)
        self.assertEqual(rent_cashflow(p,replace(lease,agent='Agent')).note,'У риелтора: Agent · за 01.01.24–01.01.24')

    def test_schedule_horizon_frequency_cap(self):
        lease=Lease(1,'Tenant','2024-01-01',id=1)
        self.assertEqual(len(scheduled_payments(lease,set(),TODAY)),4)
        self.assertEqual(scheduled_payments(replace(lease,frequency='day'),set(),TODAY),[])
        self.assertEqual(len(scheduled_payments(replace(lease,frequency='quarter'),set(),TODAY)),2)
        self.assertEqual(len(scheduled_payments(replace(lease,start_date='1900-01-01'),set(),TODAY)),601)

    def test_occupancy_merges_overlaps_and_excludes_future(self):
        acc={'created_at':'2024-03-01'}
        leases=[{'start_date':'2024-03-03','end_date':'2024-03-05'},{'start_date':'2024-03-05','end_date':'2024-03-07'},{'start_date':'2024-03-10','end_date':''},{'start_date':'2024-04-01','end_date':''}]
        result=occupancy(acc,leases,None,TODAY)
        self.assertEqual((result['total_days'],result['occupied'],result['vacant']),(15,11,4))
        self.assertEqual(result['upcoming']['start_date'],'2024-04-01')
        self.assertIsNone(result['vacant_since'])
        empty=occupancy(acc,[],None,TODAY)
        self.assertEqual(empty['vacant_since'],'2024-03-01')
        self.assertEqual(empty['rate'],0)
        self.assertEqual(occupancy({'created_at':''},[],None,TODAY)['total_days'],365)
        self.assertEqual(occupancy({'created_at':'2025-01-01'},[],None,TODAY)['total_days'],1)

    def test_payment_forex_and_overdue(self):
        rates=ExchangeRates({'USD':100})
        p=asdict(RentPayment(1,'2024-03-01',due_date='2024-03-10',amount=100,currency='USD'))
        self.assertTrue(payment_view(p,rates,TODAY)['overdue'])
        p.update(status='received',received_amount=9500,received_currency='RUB',cb_rate=90)
        result=payment_view(p,rates,TODAY)
        self.assertEqual((result['eff_rate'],result['fx_diff'],result['amount_base']),(95,500,10000))
        p.update(received_currency='EUR');self.assertIsNone(payment_view(p,rates,TODAY)['fx_diff'])

    def test_valuation_withdrawals_and_unbounded_return(self):
        self.assertIsNone(xirr([(date(2023,1,1),-1),(date(2024,1,1),100)]))
        acc={'kind':'broker','current_value':0,'currency':'RUB'}
        flows=[asdict(Cashflow(1,'2023-01-01',amount=100)),asdict(Cashflow(1,'2024-01-01','withdrawal',20)),asdict(Cashflow(1,'2024-01-01','expense',5))]
        self.assertEqual(account_metrics(acc,ExchangeRates({},'RUB'),[],flows,TODAY)['invested'],80)
        self.assertFalse(account_metrics(acc,ExchangeRates({},'bad'),[],flows,TODAY)['complete'])
