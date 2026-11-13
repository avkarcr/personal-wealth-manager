"""Stage 1 business acceptance: known subtotals, missing data and freshness."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import db
from vault import Vault
from wealth_manager.factory import create_app
from wealth_manager.bootstrap import build_services
from wealth_manager.adapters.persistence.migrations import initialize_vault
from wealth_manager.modules.accounts.domain import Account
from wealth_manager.modules.assets.domain import Asset
from wealth_manager.modules.income_sources.domain import IncomeSource
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.planning.domain import Goal, Portfolio
from wealth_manager.modules.rentals.domain import Lease, RentPayment
from wealth_manager.modules.valuation.domain import ExchangeRates, complete_sum
from wealth_manager.modules.valuation.quality import rate_factor, freshness, holding_value, issues_for
from wealth_manager.shared.records import RecordError


class PolicyTests(unittest.TestCase):
    def test_conversion_does_not_substitute_one_or_switch_base(self):
        rates=ExchangeRates({'USD':100,'EUR':125},'EUR')
        self.assertEqual(rates.to_base(10,'USD'),8)
        self.assertIsNone(rates.to_base(10,'ZZZ'))
        self.assertIsNone(rates.to_rub(None,'RUB'))
        self.assertEqual(ExchangeRates({},'ZZZ').base,'ZZZ')
        self.assertEqual(ExchangeRates({},'ZZZ').to_base(10,'ZZZ'),10)
        self.assertEqual(ExchangeRates({}).to_base(0,'ZZZ'),0)
        for value in (None,0,-1,float('nan'),float('inf'),'bad'):
            with self.subTest(rate=value):
                self.assertIsNone(rate_factor({'USD':value},'USD','RUB'))
                self.assertIsNone(rate_factor({'USD':value},'RUB','USD'))
        self.assertEqual(rate_factor({'RUB':0},'RUB','RUB'),1)
        self.assertIsNone(ExchangeRates({'USD':1e300}).to_base(1e300,'USD'))
        self.assertIsNone(holding_value(dict(quantity=1e300,price=1e300,asset_class_code='stocks',asset_class='')))
        self.assertIsNone(complete_sum([1,None,2]));self.assertEqual(complete_sum([1,2]),3)

    def test_freshness_boundaries_and_missing_prices(self):
        day=date(2026,11,11)
        for value,status in [('2026-11-04','fresh'),('2026-11-03','stale'),('2026-11-12','future'),('','undated'),(None,'undated'),('bad','undated')]:
            self.assertEqual(freshness(value,day),status)
        for price in (0,-1,None,float('nan'),float('inf')):
            self.assertIsNone(holding_value(dict(quantity=1,price=price,asset_class_code='stocks',asset_class='')))
        self.assertEqual(holding_value(dict(quantity=0,price=0,asset_class_code='stocks',asset_class='')),0)
        self.assertEqual(holding_value(dict(quantity=5,price=99,asset_class_code='cash',asset_class='')),5)
        self.assertEqual(issues_for(dict(active=False,name='X',currency='USD',value=None),'RUB',{}, {},day),[])
        self.assertEqual(issues_for(dict(quantity=0,name='X',currency='USD',value=None),'RUB',{}, {},day),[])
        source=dict(name='X',currency='USD',value=10,needs_quote=True,updated_at='2026-11-11')
        issues=issues_for(source,'EUR',{'USD':100,'EUR':120},{'USD':'2026-11-01'},day)
        self.assertEqual([(i['name'],i['status']) for i in issues],[('USD','stale'),('EUR','undated')])


class ValuationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.vault=Vault(str(Path(self.tmp.name)/'test.enc'));db.init_db(self.vault.create('password123'))
        self.vault.save(force=True);self.addCleanup(self.vault.lock)
        self.today=date.today();self.day=self.today.isoformat()
        self.s=build_services(self.vault,today=lambda:self.today)
        self.aid=self.s['save_financial_account'].execute(Account('Broker'))
        self.hid=self.s['positions'].save(Position(self.aid,name='Known stock',quantity=2,price=50,avg_price=40))
        self.gid=self.s['planning'].save_goal(Goal('Goal',Decimal('1000')))
        self.pid=self.s['planning'].save_portfolio(Portfolio('Portfolio',self.gid))
        self.app=create_app({'TESTING':True},vault=self.vault);self.client=self.app.test_client()
        with self.client.session_transaction() as session:session['vault']=self.vault.token
    def dump(self):return list(self.vault.con.iterdump())
    def snapshots(self):return list(self.vault.con.execute('SELECT * FROM snapshots'))

    def test_unknown_currency_and_price_give_partial_totals_not_fake_losses(self):
        fx=self.s['positions'].save(Position(self.aid,name='No FX',currency='ZZZ',quantity=3,price=10))
        quote=self.s['positions'].save(Position(self.aid,name='No price',quantity=4,price=0))
        for hid in (self.hid,fx,quote):self.s['planning'].allocate(self.pid,'holding',hid,'100')
        total=self.s['reports'].dashboard()['total']
        self.assertEqual(total['value'],100);self.assertFalse(total['complete']);self.assertFalse(total['value_complete'])
        self.assertIsNone(total['profit']);self.assertIsNone(total['xirr']);self.assertIsNone(total['profit_pct'])
        detail=self.s['reports'].account(self.aid)[1]
        self.assertEqual(sum(r['value'] is None for r in detail['rows']),2)
        goal=self.s['planning_reports'].overview()['goals'][0]
        self.assertIsNone(goal['total']);self.assertEqual(goal['known_total'],100);self.assertIsNone(goal['progress'])
        self.s['market_data'].save_rates({'ZZZ':2})
        self.s['positions'].update_prices(self.aid,{quote:5})
        total=self.s['reports'].dashboard()['total'];goal=self.s['planning_reports'].overview()['goals'][0]
        self.assertTrue(total['complete']);self.assertEqual(total['value'],180)
        self.assertEqual(goal['total'],180);self.assertEqual(goal['progress'],18)

    def test_missing_base_preserved_and_native_currency_needs_no_fx(self):
        self.s['market_data'].save_rates({'USD':100},'USD')
        self.s['positions'].save(Position(self.aid,id=self.hid,name='Dollars',quantity=10,asset_class_code='cash',currency='USD'))
        self.s['planning'].save_goal(Goal('Goal',Decimal('100'),currency='USD',id=self.gid))
        self.s['planning'].allocate(self.pid,'holding',self.hid,'100')
        self.s['market_data'].delete_rate('USD')
        self.assertEqual(self.s['reports'].navigation()['base'],'USD')
        self.assertEqual(self.s['reports'].dashboard()['total']['value'],10)
        self.assertEqual(self.s['planning_reports'].overview()['goals'][0]['total'],10)
        self.s['positions'].save(Position(self.aid,name='RUB cash',quantity=50,asset_class_code='cash'))
        self.assertFalse(self.s['reports'].dashboard()['total']['complete'])
        self.assertEqual(self.s['reports'].dashboard()['hist'][-1]['value'],10)  # Saved historical FX survives current-rate deletion.

    def test_income_pending_operations_and_manual_history_with_missing_rate(self):
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',10))
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',20,currency='ZZZ'))
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',30,currency='ZZZ',pending=True))
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'deposit',100,currency='ZZZ'))
        inc=self.s['reports'].income()['inc'];op=self.s['reports'].operations()
        self.assertEqual(inc['totals']['income'],10);self.assertFalse(inc['complete'])
        self.assertIsNone(inc['pending'][0]['amount']);self.assertFalse(op['complete'])
        self.assertEqual(op['totals']['income'],10)
        before=self.dump()
        with self.assertRaises(RecordError):self.s['accounts'].record_history(self.aid,self.day,1,'ZZZ')
        self.assertEqual(self.dump(),before)

    def test_missing_property_rental_and_work_income_render_safely(self):
        aid=self.s['save_asset'].execute(Asset('Property',currency='ZZZ',current_value=Decimal('1000')))
        lid,_=self.s['rentals'].save_lease(Lease(aid,'Tenant',self.day,rent_amount=10,currency='ZZZ'))
        self.s['rentals'].save_payment(RentPayment(lid,self.day,amount=10,currency='ZZZ',due_date=self.day,status='agent'))
        work=self.s['save_income_source'].execute(IncomeSource('Work',currency='ZZZ',tax_rate=Decimal(6)))
        self.s['cashflows'].save(Cashflow(work,self.day,'income',10,currency='ZZZ'))
        template,context=self.s['reports'].account(aid)
        self.assertIsNone(context['rent']['summary']['agent']);self.assertFalse(context['m']['value_complete'])
        self.assertIsNone(self.s['reports'].account(work)[1]['tax_est'])
        for url in (f'/assets/{aid}',f'/income-sources/{work}','/','/operations','/income'):
            self.assertEqual(self.client.get(url).status_code,200,url)

    def test_unknown_rows_are_not_saved_as_zero_snapshots(self):
        before=[tuple(r) for r in self.snapshots()]
        self.s['positions'].save(Position(self.aid,name='Unknown',quantity=1,price=0))
        self.assertEqual([tuple(r) for r in self.snapshots()],before)
        # Writes themselves remain available; an incomplete snapshot is skipped.
        self.assertEqual(len(self.s['reports'].account(self.aid)[1]['rows']),2)
        self.vault.lock();self.vault.unlock('password123')
        self.assertFalse(self.s['reports'].dashboard()['total']['complete'])

    def test_stale_undated_future_quotes_are_included_and_reported(self):
        old=(self.today-timedelta(days=8)).isoformat()
        self.vault.con.execute('UPDATE holdings SET updated_at=? WHERE id=?',(old,self.hid));self.vault.con.commit()
        self.s['planning'].allocate(self.pid,'holding',self.hid,'100')
        self.assertEqual(self.s['reports'].dashboard()['total']['value'],100)
        self.assertIn('stale',[i['status'] for i in self.s['reports'].dashboard()['valuation_quality']])
        self.assertIn('stale',[i['status'] for i in self.s['planning_reports'].detail(self.pid)['valuation_quality']])
        self.s['positions'].save(Position(self.aid,id=self.hid,name='Renamed',quantity=3,price=50,avg_price=40))
        self.assertEqual(self.vault.con.execute('SELECT updated_at FROM holdings WHERE id=?',(self.hid,)).fetchone()[0],old)
        self.s['positions'].update_prices(self.aid,{self.hid:50})
        self.assertFalse(self.s['reports'].dashboard()['valuation_quality'])
        self.vault.con.execute("UPDATE holdings SET updated_at='' WHERE id=?",(self.hid,));self.vault.con.commit()
        self.assertEqual(self.s['reports'].dashboard()['valuation_quality'][0]['status'],'undated')

    def test_valuation_date_updates_on_property_value_not_name(self):
        aid=self.s['save_asset'].execute(Asset('Home',current_value=Decimal(100)))
        self.vault.con.execute("UPDATE assets SET valued_at='2020-01-01' WHERE id=?",(aid,));self.vault.con.commit()
        self.s['save_asset'].execute(Asset('Renamed',current_value=Decimal(100),id=aid))
        self.assertEqual(self.vault.con.execute('SELECT valued_at FROM assets WHERE id=?',(aid,)).fetchone()[0],'2020-01-01')
        self.s['accounts'].set_value(aid,100)
        self.assertEqual(self.vault.con.execute('SELECT valued_at FROM assets WHERE id=?',(aid,)).fetchone()[0],self.day)

    def test_both_languages_pages_are_read_only_and_show_partial_labels(self):
        hid=self.s['positions'].save(Position(self.aid,name='<script>Unknown</script>',quantity=1,price=0))
        self.s['planning'].allocate(self.pid,'holding',hid,'100')
        self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',5,currency='ZZZ'))
        for lang,label in [('ru','Известная часть'),('en','Known subtotal')]:
            self.s['change_language'].execute(lang);before=self.dump();encrypted=Path(self.vault.path).read_bytes()
            for url in ('/',f'/accounts/{self.aid}','/operations','/income','/settings','/portfolios',f'/portfolios/{self.pid}'):
                response=self.client.get(url);self.assertEqual(response.status_code,200,url)
                self.assertNotIn('<script>Unknown</script>',response.get_data(as_text=True))
            self.assertIn(label,self.client.get(f'/portfolios/{self.pid}').get_data(as_text=True))
            self.assertEqual(before,self.dump());self.assertEqual(encrypted,Path(self.vault.path).read_bytes())

    def test_native_crypto_quote_does_not_require_unused_usd_conversion(self):
        from unittest.mock import Mock
        from wealth_manager.modules.market_data.domain import Quote
        self.s['market_data'].save_preferences('RU',['okx'])
        self.s['positions'].save(Position(self.aid,id=self.hid,name='BTC',ticker='BTC',quantity=1,price=1,currency='USD'))
        self.s['market_data'].delete_rate('USD')
        provider=Mock();provider.quote.return_value=Quote(50,'USDT','okx');self.s['market_data'].crypto=provider
        self.assertEqual(self.s['market_data'].refresh_account(self.aid,'crypto')['count'],1)
        self.assertEqual(self.vault.con.execute('SELECT price FROM holdings WHERE id=?',(self.hid,)).fetchone()[0],50)

    def test_cbr_published_date_does_not_become_fetch_date(self):
        from unittest.mock import Mock
        cbr=Mock();cbr.rates.return_value=({'USD':99},'01.01.2020')
        self.s['market_data'].cbr=cbr;self.s['market_data'].refresh_cbr()
        self.assertEqual(self.vault.con.execute("SELECT updated_at FROM rates WHERE currency='USD'").fetchone()[0],'2020-01-01')


class ValuationMigrationTests(unittest.TestCase):
    def test_v7_migration_does_not_invent_property_dates_and_rolls_back(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        db._init_legacy(con)
        con.execute("INSERT INTO accounts(name,kind,current_value) VALUES ('Home','realty',100)");con.commit()
        db._migrate_entities(con);db._migrate_planning(con);db._migrate_research(con);db._migrate_classifications(con)
        before=list(con.iterdump());original=db._migrate_valuation_dates
        def fail(connection):original(connection);raise OSError('Failure after migration')
        with patch('db._migrate_valuation_dates',fail):
            with self.assertRaises(OSError):db.init_db(con)
        self.assertEqual(list(con.iterdump()),before)
        db.init_db(con)
        self.assertEqual(tuple(con.execute('SELECT current_value,valued_at FROM assets').fetchone()),(100,''))
        after=list(con.iterdump());db.init_db(con);self.assertEqual(list(con.iterdump()),after)
