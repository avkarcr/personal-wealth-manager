"""Stage 10 composes existing facts and keeps unknown and future values explicit."""
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal as D
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from vault_fixtures import PortfolioCase
from wealth_manager.modules.macroeconomics.reports import Reports,REVIEW_DAYS
from wealth_manager.modules.macroeconomics.domain import Observation,INDICATORS
from wealth_manager.modules.taxes.domain import TaxEntry,Deduction
from wealth_manager.modules.liabilities.domain import Liability
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.planning.domain import Goal,Portfolio
from wealth_manager.shared.records import RecordError


class MacroOverviewTests(unittest.TestCase):
    def row(self,rid=1,country='RU',indicator='key_rate',day='2026-11-01',value='10'):
        return dict(id=rid,country=country,indicator=indicator,date=day,value=D(value),source='Manual',note='')
    def report(self,rows):return Reports(Mock(read=lambda:rows),lambda:date(2026,11,16))
    def test_current_previous_country_and_future_isolation(self):
        rows=[self.row(1,day='2026-10-01',value='12'),self.row(2,value='10'),self.row(3,day='2026-12-01',value='99'),
              self.row(4,country='US',value='4'),self.row(5,indicator='gdp_yoy',value='-1')]
        report=self.report(rows).overview('ru');rate=next(r for r in report['summary'] if r['indicator']=='key_rate')
        self.assertEqual(rate['latest']['value'],10);self.assertEqual(rate['change'],-2)
        self.assertEqual(rate['previous']['date'],'2026-10-01')
        self.assertEqual(len(rate['history']),2);self.assertEqual(report['future_count'],1)
        self.assertEqual(len(report['missing']),4);self.assertEqual(report['countries'],['RU','US'])
        self.assertEqual(self.report(rows).overview('US')['summary'][0]['latest']['value'],4)
        self.assertIsNone(self.report(rows).overview('US')['summary'][0]['change'])
        self.assertEqual(len(self.report(rows).overview()['summary']),3)
        with self.assertRaises(RecordError):self.report(rows).overview('RUS')
    def test_missing_is_not_zero_and_review_thresholds_are_explicit(self):
        empty=self.report([]).overview('RU');self.assertEqual(empty['summary'],[]);self.assertEqual(len(empty['missing']),6)
        future=self.report([self.row(day='2027-01-01')]).overview('RU')
        self.assertEqual(future['summary'],[]);self.assertEqual(future['future_count'],1)
        self.assertEqual(self.report([]).overview('JP')['countries'],['JP','RU'])
        today=date(2026,11,16)
        for code,days in REVIEW_DAYS.items():
            with self.subTest(indicator=code):
                row=self.row(indicator=code,day=(today-timedelta(days=days)).isoformat(),value='0')
                self.assertFalse(self.report([row]).overview()['summary'][0]['needs_review'])
                row['date']=(today-timedelta(days=days+1)).isoformat()
                report=self.report([row]).overview()
                self.assertTrue(report['summary'][0]['needs_review']);self.assertEqual(report['review_count'],1)
                self.assertEqual(report['summary'][0]['latest']['value'],0)


class DashboardOverviewTests(PortfolioCase):
    def report(self):return self.s['dashboard'].overview()
    def seed(self):
        y=self.today.year
        self.s['planning'].allocate(self.pid,'holding',self.hid,60)
        second=self.s['planning'].save_portfolio(Portfolio('Second',self.gid))
        self.s['planning'].allocate(second,'holding',self.hid,40)
        self.s['taxes'].save_entry(TaxEntry(y,'RU','Income','RUB',D(1000),D(13),D(30)))
        self.s['taxes'].save_entry(TaxEntry(y,'US','Income','USD',D(1000),D(10),D(120)))
        self.s['taxes'].save_entry(TaxEntry(y-1,'RU','Old','RUB',D(1000),D(10)))
        self.s['taxes'].save_entry(TaxEntry(y+1,'RU','Future','RUB',D(1000),D(10)))
        self.s['taxes'].save_deduction(Deduction(y,'RU','Education','RUB',D(100),D(13),actual_refund=D(3)))
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',D(20)))
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'expense',D(5)))
        self.s['macroeconomics'].save(Observation('RU','key_rate',self.day,D(10),'Manual'))
    def test_combined_figures_preserve_meaning_currencies_and_years(self):
        self.seed();base=self.s['reports'].dashboard();report=self.report()
        self.assertEqual(report['total'],base['total']);self.assertEqual(report['total']['value'],100)
        self.assertEqual(report['goal_overview'][0]['total'],100)
        self.assertEqual(report['inc']['totals']['net'],15)
        self.assertEqual({r['currency'] for r in report['tax_overview']},{'RUB','USD'})
        self.assertEqual(next(r for r in report['tax_overview'] if r['country']=='RU')['balance'],100)
        self.assertEqual(next(r for r in report['tax_overview'] if r['country']=='US')['balance'],-20)
        self.assertEqual(next(r for r in report['tax_overview'] if r['country']=='RU')['refund_expected'],10)
        self.assertEqual(len(report['older_tax_balances']),1)
        self.assertEqual(report['older_tax_balances'][0]['year'],self.today.year-1)
        self.assertEqual(report['macro']['summary'][0]['latest']['value'],10)
    def test_missing_price_and_fx_propagate_to_net_worth_goals_income(self):
        self.seed();self.s['positions'].update_prices(self.aid,{self.hid:D(0)})
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',D(1),currency='ZZZ'))
        report=self.report();self.assertIsNone(report['total']['net_worth'])
        self.assertIsNone(report['goal_overview'][0]['total'])
        self.assertIsNone(report['goal_overview'][0]['forecast']['required_monthly'])
        self.assertFalse(report['inc']['complete']);self.assertIsNone(report['inc']['totals']['net'])
        self.assertTrue(report['valuation_quality'])
        self.s['positions'].update_prices(self.aid,{self.hid:D(50)})
        self.s['liabilities'].create(Liability('Unknown FX',self.day,D(100),currency='ZZZ',request_id='missing-fx'))
        self.assertIsNone(self.report()['total']['net_worth']);self.assertEqual(self.report()['debts']['missing'],['ZZZ'])
    def test_debt_deadlines_overdue_due_today_30_days_and_voids(self):
        for offset in (-1,0,30,31):
            self.s['liabilities'].create(Liability('Debt '+str(offset),(self.today-timedelta(days=2)).isoformat(),D(10),due_date=(self.today+timedelta(days=offset)).isoformat(),request_id=str(offset)))
        voided=self.s['liabilities'].create(Liability('Void',self.day,D(10),due_date=self.day,request_id='void'))
        self.s['liabilities'].cancel(voided)
        report=self.report();self.assertEqual([d['name'] for d in report['debt_deadlines']],['Debt -1','Debt 0','Debt 30'])
        self.assertEqual([d['overdue'] for d in report['debt_deadlines']],[True,False,False])
        self.assertEqual(report['total']['net_worth'],60)
    def test_read_only_locales_navigation_escaping_and_filters(self):
        self.seed();self.s['planning'].save_goal(Goal('<script>goal</script>',D(1000),id=self.gid,due_date=self.day))
        self.s['macroeconomics'].save(Observation('RU','gdp_yoy',self.day,D(-1),'<script>source</script>','<script>note</script>'))
        self.s['macroeconomics'].save(Observation('US','key_rate',self.day,D(4),'Other source'))
        for lang,label in [('ru','Макроэкономический обзор'),('en','Macroeconomic overview')]:
            self.s['change_language'].execute(lang);before=self.dump();disk=Path(self.vault.path).read_bytes()
            with patch('wealth_manager.adapters.market_data.providers.HttpClient.get',side_effect=AssertionError('Network must not be used')):
                for path in ('/','/?macro_country=US','/macroeconomics','/macroeconomics?country=US'):
                    r=self.client.get(path);self.assertEqual(r.status_code,200,path)
                    self.assertNotRegex(r.get_data(as_text=True),r'<script>(goal|source|note)</script>')
            page=self.client.get('/').get_data(as_text=True);self.assertIn(label,page)
            self.assertIn(f'/goals/{self.gid}',page);self.assertIn('taxes?year=',page)
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        for path in ('/?macro_country=INVALID','/macroeconomics?country=INVALID'):self.assertEqual(self.client.get(path).status_code,400)
    def test_empty_investment_state_still_exposes_goals_taxes_and_macro(self):
        self.s['positions'].delete(self.hid);self.s['accounts'].delete(self.aid)
        self.s['macroeconomics'].save(Observation('RU','key_rate',(self.today+timedelta(days=1)).isoformat(),D(99),'Future'))
        report=self.report();self.assertFalse(report['has_any']);self.assertEqual(len(report['goal_overview']),1)
        self.assertEqual(report['tax_overview'],[]);self.assertEqual(report['macro']['summary'],[])
        self.assertEqual(report['macro']['future_count'],1)
        for path in ('/','/macroeconomics'):
            response=self.client.get(path);self.assertEqual(response.status_code,200)
            self.assertIn('Нет текущих данных',response.get_data(as_text=True))
