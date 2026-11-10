from datetime import date, timedelta
import sqlite3
import unittest
import calc
from db import init_db

class FinancialRegressions(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        init_db(self.db)
        from wealth_manager.adapters.persistence.entities import SqliteAssetRepository
        from wealth_manager.modules.assets.domain import Asset
        from decimal import Decimal
        self.aid=SqliteAssetRepository(self.db).save(Asset('Home',current_value=Decimal('1200')))
    def tearDown(self): self.db.close()
    def flow(self,kind,amount,pending=0):
        self.db.execute("INSERT INTO cashflows(account_id,date,type,amount,currency,pending) VALUES (?,?,?,?,?,?)",(self.aid,date.today().isoformat(),kind,amount,'RUB',pending))
    def metrics(self):
        acc=self.db.execute('SELECT * FROM accounts WHERE id=?',(self.aid,)).fetchone()
        return calc.account_metrics(self.db,acc,calc.Rates(self.db))

    def test_xirr_known_positive_negative_and_invalid_cashflows(self):
        start=date(2023,1,1);end=start+timedelta(days=365)
        self.assertAlmostEqual(calc.xirr([(start,-1000),(end,1100)]),0.1,places=6)
        self.assertAlmostEqual(calc.xirr([(start,-1000),(end,800)]),-0.2,places=6)
        for flows in ([],[(start,100)],[(start,-100),(start+timedelta(days=5),110)],[(start,100),(end,200)]):
            self.assertIsNone(calc.xirr(flows))

    def test_property_income_expenses_and_pending_payments(self):
        self.flow('deposit',1000);self.flow('income',100);self.flow('expense',25);self.flow('income',500,pending=1)
        m=self.metrics()
        self.assertEqual((m['value'],m['invested'],m['profit'],m['income']),(1200,1000,275,100))

    def test_broker_income_not_counted_twice(self):
        self.db.execute('DELETE FROM entities WHERE id=?',(self.aid,))
        from wealth_manager.adapters.persistence.entities import SqliteFinancialAccountRepository
        from wealth_manager.modules.accounts.domain import Account
        self.aid=SqliteFinancialAccountRepository(self.db).save(Account('Broker'))
        self.db.execute("INSERT INTO holdings(account_id,name,quantity,avg_price,price) VALUES (?,'Stock',10,100,120)",(self.aid,))
        self.flow('deposit',1000);self.flow('income',50)
        self.assertEqual(self.metrics()['profit'],200)

    def test_no_deposits_uses_cost_basis(self):
        self.db.execute('DELETE FROM entities WHERE id=?',(self.aid,))
        from wealth_manager.adapters.persistence.entities import SqliteFinancialAccountRepository
        from wealth_manager.modules.accounts.domain import Account
        self.aid=SqliteFinancialAccountRepository(self.db).save(Account('Broker'))
        self.db.execute("INSERT INTO holdings(account_id,name,quantity,avg_price,price) VALUES (?,'Stock',10,100,120)",(self.aid,))
        m=self.metrics()
        self.assertEqual(m['invested'],1000)
        self.assertTrue(m['invested_from_cost']);self.assertIsNone(m['xirr'])

    def test_currency_conversion_and_base_currency(self):
        self.db.execute("UPDATE rates SET rate=100 WHERE currency='USD'")
        self.db.execute("UPDATE settings SET value='USD' WHERE key='base_currency'")
        rates=calc.Rates(self.db)
        self.assertEqual(rates.to_base(1000,'RUB'),10)
        self.assertEqual(rates.to_base(10,'USD'),10)
        self.assertEqual(rates.rub_to_base(500),5)

    def lease(self):
        lid=self.db.execute("INSERT INTO leases(asset_id,tenant,start_date,end_date,rent_amount,currency,pay_day) VALUES (?,'Tenant','2024-01-31','2024-03-31',100,'USD',31)",(self.aid,)).lastrowid
        return self.db.execute('SELECT * FROM leases WHERE id=?',(lid,)).fetchone()

    def test_schedule_is_idempotent_and_clamps_leap_year_dates(self):
        lease=self.lease()
        self.assertEqual(calc.generate_schedule(self.db,lease),3)
        self.assertEqual(calc.generate_schedule(self.db,lease),0)
        rows=self.db.execute('SELECT * FROM rent_payments ORDER BY period_start').fetchall()
        self.assertEqual(rows[1]['due_date'],'2024-02-29')
        self.assertEqual(rows[-1]['period_end'],'2024-03-31')
        self.assertEqual(calc.add_months(date(2023,1,31),1),date(2023,2,28))

    def test_rent_status_transitions_do_not_duplicate_income(self):
        calc.generate_schedule(self.db,self.lease())
        pid=self.db.execute('SELECT id FROM rent_payments LIMIT 1').fetchone()[0]
        calc.sync_rent_cashflow(self.db,pid)
        self.assertEqual(self.db.execute('SELECT count(*) FROM cashflows').fetchone()[0],0)
        self.db.execute("UPDATE rent_payments SET status='agent' WHERE id=?",(pid,));calc.sync_rent_cashflow(self.db,pid)
        row=self.db.execute('SELECT * FROM cashflows').fetchone()
        self.assertEqual(row['pending'],1)
        self.db.execute("UPDATE rent_payments SET status='received',received_amount=9500,received_currency='RUB' WHERE id=?",(pid,))
        calc.sync_rent_cashflow(self.db,pid);calc.sync_rent_cashflow(self.db,pid)
        rows=self.db.execute('SELECT * FROM cashflows').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['pending'],rows[0]['amount'],rows[0]['currency']),(0,9500,'RUB'))
        self.db.execute("UPDATE rent_payments SET status='expected' WHERE id=?",(pid,));calc.sync_rent_cashflow(self.db,pid)
        self.assertEqual(self.db.execute('SELECT count(*) FROM cashflows').fetchone()[0],0)
