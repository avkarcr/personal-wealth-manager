"""Monthly scenarios, deadlines and one contribution per goal across portfolios."""
from dataclasses import asdict,replace
from datetime import date,timedelta
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_valuation_quality
from wealth_manager.modules.planning.domain import Goal,Portfolio,PlanningError
from wealth_manager.modules.planning.forecast import forecast,month_date
from wealth_manager.modules.positions.domain import Position
from wealth_manager.shared.errors import EntityNotFound

class ForecastDomainTests(unittest.TestCase):
    today=date(2026,1,31)
    def goal(self,**kw):
        return dict(target_amount=D(1200),monthly_contribution=D(100),annual_return=D(0),annual_inflation=D(0),due_date='2027-01-31',**kw)
    def test_zero_return_contributions_arrive_at_month_end_and_not_today(self):
        f=forecast(self.goal(),D(0),self.today)
        self.assertEqual(f['required_monthly'],100);self.assertEqual(f['projected'],1200)
        self.assertEqual(f['reached_date'],'2027-01-31');self.assertTrue(f['on_track'])
        self.assertEqual(f['contributions'],1200)
        self.assertEqual(f['timeline'][0]['value'],0);self.assertEqual(f['timeline'][1]['value'],100)
        self.assertEqual(f['timeline'][1]['date'],'2026-02-28')
        self.assertEqual(month_date(self.today,2),date(2026,3,31))
        self.assertEqual(month_date(date(2024,1,31),1),date(2024,2,29))
    def test_effective_annual_growth_and_inflation_reanchor_exactly_at_year_end(self):
        g=self.goal();g.update(target_amount=D(110),monthly_contribution=D(0),annual_return=D(10))
        f=forecast(g,D(100),self.today)
        self.assertEqual(f['projected'],110);self.assertEqual(f['reached_date'],'2027-01-31');self.assertEqual(f['required_monthly'],0)
        g.update(target_amount=D(100),annual_inflation=D(10))
        f=forecast(g,D(100),self.today)
        self.assertEqual(f['target_at_deadline'],110);self.assertEqual(f['shortfall'],0)
        self.assertEqual(f['timeline'][-1]['value'],f['timeline'][-1]['target'])
    def test_needed_contribution_rounds_up_and_actually_meets_deadline(self):
        g=self.goal();g.update(target_amount=D('1000.01'),annual_return=D(7),annual_inflation=D(4),monthly_contribution=D(0))
        f=forecast(g,D(100),self.today);needed=f['required_monthly']
        g['monthly_contribution']=needed
        self.assertTrue(forecast(g,D(100),self.today)['on_track'])
        g['monthly_contribution']=needed-D('.01')
        self.assertFalse(forecast(g,D(100),self.today)['on_track'])
    def test_rounding_step_supports_crypto_precision_and_budget_increments(self):
        g=self.goal();g.update(target_amount=D('0.001'),monthly_contribution=0,contribution_step=D('0.00000001'))
        self.assertEqual(forecast(g,D(0),self.today)['required_monthly'],D('0.00008334'))
        g.update(target_amount=D('1000'),contribution_step=D(100))
        self.assertEqual(forecast(g,D(0),self.today)['required_monthly'],100)
        with self.assertRaises(PlanningError):Goal('X',D(100),contribution_step=D(0)).validate()
        g['contribution_step']=0
        self.assertEqual(forecast(g,D(0),self.today)['state'],'invalid')

    def test_negative_return_zero_contribution_and_increasing_target(self):
        g=self.goal();g.update(target_amount=D(100),monthly_contribution=D(0),annual_return=D(-10))
        f=forecast(g,D(100),self.today)
        self.assertEqual(f['reached_date'],self.today.isoformat());self.assertFalse(f['on_track'])
        self.assertEqual(f['projected'],90);self.assertGreater(f['required_monthly'],0)
        g.update(target_amount=D(1000),annual_return=D(0),annual_inflation=D(10))
        f=forecast(g,D(100),self.today);self.assertIsNone(f['reached_date'])
        self.assertEqual(f['target_at_deadline'],1100)
    def test_deadlines_no_complete_month_expired_absent_and_beyond_horizon(self):
        for due in ('2026-01-31','2026-02-27'):
            g=self.goal();g['due_date']=due;f=forecast(g,D(100),self.today)
            self.assertEqual(f['months'],0);self.assertIsNone(f['required_monthly']);self.assertEqual(f['shortfall'],1100)
            self.assertEqual(len(f['timeline']),1)
        g=self.goal();g['due_date']='2026-02-28'
        self.assertEqual(forecast(g,D(100),self.today)['months'],1)
        g['due_date']='2025-12-31';f=forecast(g,D(100),self.today)
        self.assertTrue(f['expired']);self.assertIsNone(f['projected']);self.assertIsNone(f['required_monthly'])
        for due in ('','9999-12-31'):
            g['due_date']=due;f=forecast(g,D(100),self.today)
            self.assertFalse(f['deadline_supported']);self.assertIsNone(f['required_monthly']);self.assertEqual(f['reached_date'],'2026-12-31')
    def test_missing_negative_and_out_of_budget_data_do_not_produce_false_dates(self):
        for value in (None,D(-1),D('1e61')):
            f=forecast(self.goal(),value,self.today);self.assertNotEqual(f['state'],'ready');self.assertIsNone(f['reached_date'])
        for key,value in [('target_amount',D('1e100')),('monthly_contribution',D(-1)),('annual_return',D(-100)),('annual_inflation',D(-1))]:
            g=self.goal();g[key]=value;self.assertEqual(forecast(g,D(100),self.today)['state'],'invalid')
    def test_goal_validation_bounds_and_nonfinite_values(self):
        g=Goal('Goal',D(100))
        for change in (dict(monthly_contribution=-1),dict(annual_return=-100),dict(annual_return=101),dict(annual_inflation=-1),dict(annual_inflation=101),dict(target_amount='1e100')):
            with self.subTest(change=change),self.assertRaises((PlanningError,ValueError)):replace(g,**change).validate()
        with self.assertRaises(ValueError):replace(g,annual_return='NaN')
        replace(g,annual_return=-99,annual_inflation=100).validate()

class ForecastIntegrationTests(unittest.TestCase):
    setUp=test_valuation_quality.ValuationIntegrationTests.setUp
    dump=test_valuation_quality.ValuationIntegrationTests.dump
    def test_multiple_portfolios_share_one_plan_and_do_not_mutate_money(self):
        p2=self.s['planning'].save_portfolio(Portfolio('Second',self.gid))
        self.s['planning'].allocate(self.pid,'holding',self.hid,60)
        self.s['planning'].allocate(p2,'holding',self.hid,40)
        g=Goal('Goal',D(1300),due_date=month_date(self.today,12).isoformat(),id=self.gid,monthly_contribution=D(100))
        wealth=self.s['reports'].dashboard()
        self.s['planning'].save_goal(g)
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        goal=self.s['planning_reports'].goal_detail(self.gid)['goal']
        self.assertEqual(goal['total'],100);self.assertEqual(goal['forecast']['projected'],1300)
        self.assertEqual(goal['forecast']['contributions'],1200)
        self.assertEqual(self.s['reports'].dashboard(),wealth)
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.s['planning_reports'].goal_detail(self.gid)['goal']['monthly_contribution'],100)
    def test_missing_quote_fx_and_reallocation_recompute_plan(self):
        self.s['planning'].allocate(self.pid,'holding',self.hid,100)
        self.s['positions'].update_prices(self.aid,{self.hid:D(0)})
        goal=self.s['planning_reports'].goal_detail(self.gid)['goal']
        self.assertEqual(goal['forecast']['state'],'missing');self.assertIsNone(goal['forecast']['required_monthly'])
        self.s['positions'].update_prices(self.aid,{self.hid:D(50)})
        self.s['planning'].save_goal(Goal('Goal',D(1000),currency='ZZZ',id=self.gid))
        self.assertEqual(self.s['planning_reports'].goal_detail(self.gid)['goal']['forecast']['state'],'missing')
        self.s['planning'].save_goal(Goal('Goal',D(1000),id=self.gid))
        self.s['planning'].allocate(self.pid,'holding',self.hid,50)
        self.assertEqual(self.s['planning_reports'].goal_detail(self.gid)['goal']['total'],50)
        with self.assertRaises(EntityNotFound):self.s['planning_reports'].goal_detail(999)
    def test_saved_plan_rolls_back_on_disk_error(self):
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        with patch('vault.os.replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.s['planning'].save_goal(Goal('Goal',D(1000),id=self.gid,monthly_contribution=D(100)))
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
    def test_http_locales_forms_read_only_and_escaping(self):
        self.s['planning'].allocate(self.pid,'holding',self.hid,100)
        data=dict(id=self.gid,name='<script>x</script>',target_amount='1000',currency='RUB',due_date=month_date(self.today,12).isoformat(),monthly_contribution='100',annual_return='5',annual_inflation='3')
        self.assertEqual(self.client.post('/goals/save',data=data).status_code,302)
        for lang,label in [('ru','План и прогноз цели'),('en','Goal plan and forecast')]:
            self.s['change_language'].execute(lang);before=self.dump();disk=Path(self.vault.path).read_bytes()
            for path in (f'/goals/{self.gid}',f'/portfolios/{self.pid}','/portfolios','/settings'):
                response=self.client.get(path);self.assertEqual(response.status_code,200)
                self.assertNotIn('<script>x</script>',response.get_data(as_text=True))
            self.assertIn(label,self.client.get(f'/goals/{self.gid}').get_data(as_text=True))
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        self.assertEqual(self.client.get('/goals/999').status_code,404)
        self.assertEqual(self.client.post('/goals/save',data=dict(data,annual_return='-100')).status_code,400)

class ForecastMigrationTests(unittest.TestCase):
    def test_schema13_default_zero_assumptions_and_atomic_migration(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        with patch('db._migrate_goal_forecasts'),patch('db._migrate_instruments'),patch('db._migrate_macro_versions'),patch('db._migrate_macro_calculations'):db.init_db(c)
        c.execute("INSERT INTO goals(name,target_amount,currency) VALUES ('Old','1000','RUB')");c.commit()
        before=list(c.iterdump());original=db._migrate_goal_forecasts
        def fail(con):original(con);raise RuntimeError('migration')
        with patch('db._migrate_goal_forecasts',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(c)
        self.assertEqual(list(c.iterdump()),before)
        db.init_db(c)
        self.assertEqual(tuple(c.execute('SELECT monthly_contribution,annual_return,annual_inflation FROM goals').fetchone()),(0,0,0))
        before=list(c.iterdump());db.init_db(c);self.assertEqual(list(c.iterdump()),before)
