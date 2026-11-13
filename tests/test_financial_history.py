"""Stage 2: archive invariance, closure lifecycle and dated FX persistence."""
from datetime import date, timedelta
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import unittest
import sqlite3
import db
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.valuation.history import rates_on
from wealth_manager.modules.reporting.domain import history
from wealth_manager.shared.records import RecordError
import test_valuation_quality


class FinancialHistoryTests(unittest.TestCase):
    setUp=test_valuation_quality.ValuationIntegrationTests.setUp
    dump=test_valuation_quality.ValuationIntegrationTests.dump

    def clear_positions(self):
        self.s['positions'].save(Position(self.aid,id=self.hid,name='Empty',quantity=0,price=50))

    def test_archiving_preserves_capital_income_history_and_goal(self):
        self.s['planning'].allocate(self.pid,'holding',self.hid,'100')
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',25))
        before=self.s['reports'].dashboard()
        goal=self.s['planning_reports'].overview()['goals'][0]['total']
        self.s['accounts'].toggle_archived(self.aid)
        after=self.s['reports'].dashboard()
        for key in ('total','hist','inc'):self.assertEqual(before[key],after[key],key)
        self.assertEqual(self.s['planning_reports'].overview()['goals'][0]['total'],goal)
        self.assertNotIn(self.aid,[a['id'] for a in self.s['reports'].navigation()['nav_accounts']])
        self.s['accounts'].toggle_archived(self.aid)
        self.assertEqual(before['total'],self.s['reports'].dashboard()['total'])

    def test_close_requires_empty_positions_and_resolved_flows(self):
        with self.assertRaises(RecordError):self.s['accounts'].close(self.aid)
        self.clear_positions()
        fid=self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',5,pending=True))
        with self.assertRaises(RecordError):self.s['accounts'].close(self.aid)
        self.s['cashflows'].delete(fid)
        fid=self.s['cashflows'].save(Cashflow(self.aid,(self.today+timedelta(days=1)).isoformat(),'deposit',5))
        with self.assertRaises(RecordError):self.s['accounts'].close(self.aid)
        self.s['cashflows'].delete(fid)
        self.s['accounts'].close(self.aid);self.s['accounts'].close(self.aid)
        self.assertEqual(self.vault.con.execute('SELECT closed_at FROM accounts WHERE id=?',(self.aid,)).fetchone()[0],self.day)
        with self.assertRaises(ValueError):self.s['positions'].save(Position(self.aid,quantity=1,price=1))
        with self.assertRaises(ValueError):self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',5,pending=True))
        with self.assertRaises(ValueError):self.s['cashflows'].save(Cashflow(self.aid,(self.today+timedelta(days=1)).isoformat(),'income',5))
        # Correcting an actual old operation is permitted.
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',5))
        with self.assertRaises(RecordError):self.s['accounts'].record_history(self.aid,self.day,50)
        self.s['accounts'].reopen(self.aid)
        self.s['positions'].save(Position(self.aid,quantity=1,price=5))

    def test_received_action_cannot_move_a_flow_after_closure(self):
        self.clear_positions()
        fid=self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',5))
        self.s['accounts'].close(self.aid)
        self.today+=timedelta(days=1)
        before=self.dump()
        with self.assertRaises(ValueError):self.s['cashflows'].mark_received(fid)
        self.assertEqual(self.dump(),before)

    def test_closure_events_survive_reopening_without_fake_withdrawals(self):
        self.clear_positions()
        old=self.today-timedelta(days=4)
        self.s['accounts'].record_history(self.aid,old.isoformat(),100)
        self.today=old+timedelta(days=1)
        self.s['accounts'].close(self.aid)
        closed_day=self.today.isoformat()
        self.today+=timedelta(days=1)
        self.s['accounts'].reopen(self.aid)
        rows=self.s['reports'].account(self.aid)[1]['hist']
        values={r['date']:r['value'] for r in rows}
        self.assertEqual(values[old.isoformat()],100)
        self.assertEqual(values[closed_day],0)
        self.assertEqual(values[self.today.isoformat()],0)
        self.assertEqual(self.vault.con.execute('SELECT COUNT(*) FROM cashflows').fetchone()[0],0)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.s['reports'].account(self.aid)[1]['hist'],rows)

    def test_snapshot_and_flow_use_their_own_dates_not_current_fx(self):
        day1=(self.today-timedelta(days=2)).isoformat();day2=(self.today-timedelta(days=1)).isoformat()
        market=self.s['market_data']
        market.save_rates({'USD':100})
        self.s['cashflows'].save(Cashflow(self.aid,day1,'deposit',10,'USD'))
        before=self.dump()
        with self.assertRaises(RecordError):self.s['accounts'].record_history(self.aid,day2,20,'USD')
        self.assertEqual(self.dump(),before)
        market.save_historical_rate('USD',70,day1);market.save_historical_rate('USD',80,day2)
        self.s['accounts'].record_history(self.aid,day2,20,'USD')
        row=self.vault.con.execute('SELECT value_rub,invested_rub FROM snapshots WHERE account_id=? AND date=?',(self.aid,day2)).fetchone()
        self.assertEqual(tuple(row),(1600,700))
        market.save_rates({'USD':120},base='USD')
        latest=self.vault.con.execute('SELECT invested_rub FROM snapshots WHERE account_id=? AND date=?',(self.aid,self.day)).fetchone()
        self.assertEqual(latest[0],700)
        point=next(r for r in self.s['reports'].dashboard()['hist'] if r['date']==day2)
        self.assertEqual((point['value'],point['invested']),(20,8.75))
        market.delete_rate('USD')
        self.assertEqual(next(r for r in self.s['reports'].dashboard()['hist'] if r['date']==day2),point)

    def test_zero_net_contributions_are_not_replaced_with_valuation(self):
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'deposit',100))
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'withdrawal',100))
        self.s['accounts'].record_history(self.aid,self.day,25)
        self.assertEqual(self.vault.con.execute('SELECT invested_rub FROM snapshots WHERE account_id=? AND date=?',(self.aid,self.day)).fetchone()[0],0)

    def test_missing_historical_fx_stays_unknown_and_is_read_only(self):
        old=(self.today-timedelta(days=20)).isoformat()
        self.s['accounts'].record_history(self.aid,old,100)
        self.s['market_data'].save_rates({'USD':100},base='USD')
        point=next(r for r in self.s['reports'].dashboard()['hist'] if r['date']==old)
        self.assertIsNone(point['value']);self.assertIsNone(point['invested'])
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        for url in ('/',f'/accounts/{self.aid}','/settings'):
            self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        self.assertIsNone(rates_on([],old,'USD').rub_to_base(1))

    def test_close_and_historical_rate_rollback_on_save_failure(self):
        self.clear_positions()
        for action in (lambda:self.s['accounts'].close(self.aid),lambda:self.s['market_data'].save_historical_rate('ZZZ',5,self.day)):
            before=self.dump();disk=Path(self.vault.path).read_bytes()
            with patch('vault.os.replace',side_effect=OSError('full')):
                with self.assertRaises(OSError):action()
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)

    def test_http_and_localization(self):
        self.assertEqual(self.client.post(f'/accounts/{self.aid}/close').status_code,400)
        self.clear_positions()
        self.assertEqual(self.client.post(f'/accounts/{self.aid}/close').status_code,302)
        self.assertEqual(self.client.post('/rates/history',data=dict(currency='USD',rate='80',date=self.day)).status_code,302)
        for language,term in [('ru','Счёт закрыт'),('en','Account closed')]:
            self.s['change_language'].execute(language)
            self.assertIn(term,self.client.get(f'/accounts/{self.aid}').get_data(as_text=True))
            self.assertEqual(self.client.get('/settings').status_code,200)
        self.assertEqual(self.client.post(f'/accounts/{self.aid}/reopen').status_code,302)
        for url in ('/accounts/999/close','/accounts/999/reopen'):
            self.assertEqual(self.client.post(url).status_code,404)
        for data in (dict(currency='USD',rate='0',date=self.day),dict(currency='USD',rate='1',date='bad'),dict(currency='USD',rate='1',date=(self.today+timedelta(days=1)).isoformat())):
            self.assertEqual(self.client.post('/rates/history',data=data).status_code,400)

    def test_migration_v8_keeps_snapshots_and_does_not_backfill_rates(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        with patch('db._migrate_history'):
            db.init_db(con)
        con.execute("INSERT INTO entities(id,entity_type,name) VALUES (1,'account','Old')")
        con.execute("INSERT INTO financial_accounts(id,kind) VALUES (1,'broker')")
        con.execute("INSERT INTO snapshots(date,account_id,value_rub,invested_rub) VALUES ('2020-01-01',1,100,50)")
        con.execute("UPDATE rates SET updated_at='2026-01-01' WHERE currency='USD'");con.commit()
        before=list(con.iterdump());original=db._migrate_history
        def failed(c):original(c);raise RuntimeError('fail')
        with patch('db._migrate_history',side_effect=failed):
            with self.assertRaises(RuntimeError):db.init_db(con)
        self.assertEqual(list(con.iterdump()),before)
        db.init_db(con)
        self.assertEqual(tuple(con.execute('SELECT value_rub,invested_rub FROM snapshots').fetchone()),(100,50))
        self.assertIsNone(con.execute("SELECT 1 FROM historical_rates WHERE date='2020-01-01'").fetchone())
        self.assertEqual(con.execute('SELECT closed_at FROM accounts').fetchone()[0],'')
        before=list(con.iterdump());db.init_db(con);self.assertEqual(list(con.iterdump()),before)
