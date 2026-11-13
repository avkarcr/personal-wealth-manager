from copy import deepcopy
from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import Mock
from wealth_manager.modules.reporting.domain import portfolio, history, income_report, last_months
from wealth_manager.modules.reporting.application import Reports
from wealth_manager.modules.valuation.domain import ExchangeRates
from wealth_manager.modules.entities.domain import RecordDraft as Account
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow
import test_market_data

class ReportDomainTests(unittest.TestCase):
    def setUp(self):
        self.today=date(2026,11,10);self.rates=ExchangeRates({'USD':100})
        self.data=dict(accounts=[dict(id=1,name='Home',kind='realty',currency='RUB',current_value=1500,archived=0),dict(id=2,name='Broker',kind='broker',currency='RUB',current_value=0,archived=0),dict(id=3,name='Work',kind='freelance',currency='RUB',current_value=0,archived=0),dict(id=4,name='Old',kind='broker',currency='RUB',current_value=0,archived=1)],holdings=[],cashflows=[],snapshots=[],leases=[],rent_payments=[])
    def flow(self,aid,kind,amount,day='2026-11-01',pending=0):
        self.data['cashflows'].append(dict(id=len(self.data['cashflows'])+1,account_id=aid,type=kind,amount=amount,date=day,pending=pending,currency='RUB',category='',source=''))

    def test_income_groups_pending_expenses_and_period_boundaries(self):
        self.flow(1,'income',100);self.flow(1,'expense',20);self.flow(3,'income',200);self.flow(3,'expense',30)
        self.flow(1,'income',50,'2020-01-01',pending=1);self.flow(1,'expense',9,pending=1)
        self.flow(1,'income',1000,'2020-01-01');self.flow(4,'income',999)
        r=income_report(self.data,self.rates,self.today,year='2026')
        self.assertEqual((r['totals']['income'],r['totals']['expense'],r['totals']['freelance'],r['totals']['pending']),(1299,50,200,50))
        self.assertEqual(r['totals']['this_month'],1299);self.assertEqual(r['by_src'][0],('Old','broker',999))
        self.assertEqual(income_report(self.data,self.rates,self.today,account_id=4)['totals']['income'],999)
        with self.assertRaises(ValueError):income_report(self.data,self.rates,self.today,months=0)
        self.assertEqual(last_months(date(2026,1,1),2),['2025-12','2026-01'])

    def test_portfolio_retains_archives_and_excludes_no_deposit_accounts_from_xirr(self):
        self.flow(1,'deposit',1000,'2025-01-01');self.flow(1,'withdrawal',100);self.flow(1,'income',100);self.flow(1,'expense',20)
        self.flow(2,'income',5);self.flow(3,'income',999);self.flow(4,'deposit',10000)
        items,total,_,_=portfolio(self.data,self.rates,self.today,include_archived=True)
        self.assertEqual(len(items),3);self.assertEqual(total['value'],1500);self.assertEqual(total['invested'],10900)
        self.assertAlmostEqual(total['profit'],-9320);self.assertIsNone(total['xirr'])
        self.assertEqual(len(portfolio(self.data,self.rates,self.today)[0]),3)

    def test_history_forward_fill_and_archived_account_detail(self):
        self.data['snapshots']=[dict(account_id=1,date='2026-01-01',value_rub=100,invested_rub=80),dict(account_id=2,date='2026-02-01',value_rub=50,invested_rub=40),dict(account_id=1,date='2026-03-01',value_rub=120,invested_rub=80),dict(account_id=4,date='2026-03-01',value_rub=999,invested_rub=500)]
        self.assertEqual([r['value'] for r in history(self.data,self.rates)],[100,150,1169])
        self.assertEqual(history(self.data,self.rates,4)[0]['value'],999)

class ReportIntegrationTests(unittest.TestCase):
    setUp=test_market_data.MarketTests.setUp

    def test_pages_read_without_mutating_database_or_disk(self):
        self.s['positions'].save(Position(self.aid,ticker='BTC',quantity=2,price=10,avg_price=5))
        self.s['cashflows'].save(Cashflow(self.aid,'2026-11-01','income',50))
        self.s['cashflows'].save(Cashflow(self.aid,'2026-11-01','expense',5))
        before=list(self.vault.con.iterdump())
        with open(self.vault.path,'rb') as f:disk=f.read()
        for _ in range(2):
            for url in ('/',f'/accounts/{self.aid}','/operations','/operations?account=1&type=income&year=2026','/income?year=2026','/settings?country=RU','/export.csv'):
                self.assertEqual(self.client.get(url).status_code,200,url)
        self.assertEqual(list(self.vault.con.iterdump()),before)
        with open(self.vault.path,'rb') as f:self.assertEqual(f.read(),disk)
        result=self.s['reports'].operations(type='income',year='2026')
        self.assertEqual(result['totals']['income'],50);self.assertEqual(result['monthly'],[('2026-11',50)])
        self.assertEqual(self.client.get('/accounts/999').status_code,404)
        self.vault.lock()
        with self.assertRaises(RuntimeError):self.s['reports'].dashboard()

    def test_report_application_uses_detached_port(self):
        repository=Mock()
        repository.read.return_value=dict(accounts=[],holdings=[],cashflows=[],leases=[],rent_payments=[],rates=[],settings=[],snapshots=[])
        reports=Reports(repository,lambda:date(2026,11,10))
        self.assertFalse(reports.dashboard()['has_any']);repository.read.assert_called_once()
