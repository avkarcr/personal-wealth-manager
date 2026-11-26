"""End-to-end decimal precision, migrated storage and explicit output boundaries."""
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch
import sqlite3
import unittest
import db
import test_valuation_quality
from wealth_manager.shared.decimal import decimal, exact_text, rounded, input_decimal
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.valuation.domain import ExchangeRates, account_metrics
from wealth_manager.modules.valuation.quality import holding_value
from wealth_manager.modules.rentals.domain import Lease, RentPayment
from wealth_manager.modules.taxes.domain import estimate
from wealth_manager.adapters.market_data.providers import HttpClient
from wealth_manager.adapters.persistence.migrations import initialize_vault


class DecimalPolicyTests(unittest.TestCase):
    def test_rounding_and_input_boundaries(self):
        self.assertEqual(decimal(0.1),Decimal('0.1'))
        self.assertEqual(rounded('1.005'),Decimal('1.00'))
        self.assertEqual(rounded('1.015'),Decimal('1.02'))
        self.assertEqual(rounded('-1.015'),Decimal('-1.02'))
        self.assertEqual(exact_text(Decimal('-0.000')),'0')
        self.assertEqual(exact_text(Decimal('120.0100')),'120.01')
        self.assertEqual(input_decimal('0.000000000000000001'),Decimal('1e-18'))
        for v in ('NaN','Infinity','-Infinity','bad',None,'1e25','1e-101','1.'+'1'*80):
            with self.subTest(v=v),self.assertRaises(ValueError):input_decimal(v)
        self.assertEqual(estimate(dict(base='1.005',rate='100')),Decimal('1.01')) # Existing tax policy: HALF_UP.

    def test_core_arithmetic_ignores_ambient_decimal_context(self):
        with localcontext() as ctx:
            ctx.prec=3
            self.assertEqual(ExchangeRates({'USD':Decimal('90.123456789')}).to_rub(Decimal('123456789.123456789'),'USD'),Decimal('11126352599.876543109750190521'))
            self.assertEqual(holding_value(dict(quantity=Decimal('0.1'),price=Decimal('0.2'),asset_class_code='stocks',asset_class='')),Decimal('0.02'))
            self.assertEqual(ctx.prec,3)

    def test_provider_json_keeps_price_digits(self):
        http=HttpClient()
        with patch.object(http,'get',return_value=b'{"price":0.123456789012345678}'):
            self.assertEqual(http.json('unused')['price'],Decimal('0.123456789012345678'))


class DecimalIntegrationTests(unittest.TestCase):
    setUp=test_valuation_quality.ValuationIntegrationTests.setUp
    dump=test_valuation_quality.ValuationIntegrationTests.dump

    def test_position_http_disk_and_reopen_preserve_crypto_digits(self):
        value='0.123456789012345678'
        r=self.client.post('/holdings/save',data=dict(id=self.hid,account_id=self.aid,name='Exact',quantity=value,price='0.2',avg_price='0.1',currency='RUB'))
        self.assertEqual(r.status_code,302)
        row=self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(self.hid,)).fetchone()
        self.assertEqual(row['quantity'],Decimal(value))
        self.assertEqual(self.vault.con.execute('SELECT typeof(quantity),CAST(quantity AS TEXT) AS raw FROM holdings WHERE id=?',(self.hid,)).fetchone()[:],('text',value))
        total=self.s['reports'].dashboard()['total']
        self.assertEqual(total['value'],Decimal('0.0246913578024691356'))
        self.assertEqual(total['profit'],Decimal('0.0123456789012345678'))
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.s['reports'].dashboard()['total'],total)
        html=self.client.get('/').status_code # Session invalidation after reopening is expected.
        self.assertIn(html,(200,302))

    def test_repeated_flows_totals_and_history_are_exact(self):
        for _ in range(3):self.s['cashflows'].save(Cashflow(self.aid,self.day,'deposit',Decimal('0.1')))
        for _ in range(3):self.s['cashflows'].save(Cashflow(self.aid,self.day,'income',Decimal('0.1')))
        self.assertEqual(self.s['reports'].operations()['totals']['deposit'],Decimal('0.3'))
        self.assertEqual(self.s['reports'].income()['inc']['totals']['income'],Decimal('0.3'))
        self.s['accounts'].record_history(self.aid,self.day,Decimal('0.123456789012345678'))
        row=self.vault.con.execute('SELECT value_rub,invested_rub FROM snapshots WHERE account_id=?',(self.aid,)).fetchone()
        self.assertEqual(tuple(row),(Decimal('0.123456789012345678'),Decimal('0.3')))

    def test_rates_and_chart_json_keep_decimal_strings(self):
        value='90.123456789012345678'
        self.assertEqual(self.client.post('/rates/history',data=dict(currency='USD',rate=value,date=self.day)).status_code,302)
        self.assertEqual(self.vault.con.execute("SELECT rate FROM historical_rates WHERE currency='USD' AND date=?",(self.day,)).fetchone()[0],Decimal(value))
        self.s['market_data'].save_rates({'USD':Decimal(value)})
        self.assertEqual(self.vault.con.execute("SELECT rate FROM rates WHERE currency='USD'").fetchone()[0],Decimal(value))
        with self.app.app_context():
            self.assertEqual(self.app.json.loads(self.app.json.dumps(dict(amount=Decimal('0.123456789012345678'))))['amount'],'0.123456789012345678')

    def test_invalid_batch_is_atomic_and_invalid_amount_is_not_zero(self):
        for endpoint,data in [('/rates/save',dict(rate_USD='90.1',rate_EUR='bad')),
                              (f'/accounts/{self.aid}/prices',{f'price_{self.hid}':'1.1','price_999':'NaN'}),
                              ('/flows/save',dict(account_id=self.aid,date=self.day,type='income',amount='bad')),
                              ('/holdings/save',dict(account_id=self.aid,name='Tiny',quantity='1e-101',price='1'))]:
            before=self.dump();disk=Path(self.vault.path).read_bytes()
            self.assertEqual(self.client.post(endpoint,data=data).status_code,400,endpoint)
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)

    def test_display_rounding_does_not_change_stored_amount(self):
        money=self.app.jinja_env.filters['money']
        self.assertIn('12,34',money(Decimal('12.345'),'RUB'))
        self.assertIn('12,36',money(Decimal('12.355'),'RUB'))
        self.assertIn('0,000000000000000001',money(Decimal('1e-18'),'ETH'))
        self.assertIn('12',money(Decimal('12.4'),'JPY'))
        with localcontext() as ctx:
            ctx.prec=3
            self.assertIn('0,12345678901234567890123456789',money(Decimal('0.12345678901234567890123456789'),'ETH'))
            self.assertEqual(ctx.prec,3)
        for language in ('ru','en'):
            self.s['change_language'].execute(language)
            for url in ('/',f'/accounts/{self.aid}','/income','/settings','/operations','/portfolios'):
                self.assertEqual(self.client.get(url).status_code,200,(language,url))

    def test_rental_income_and_fx_difference_keep_cents(self):
        from wealth_manager.modules.entities.domain import RecordDraft
        aid=self.s['save_account'].execute(RecordDraft('Home',kind='realty',current_value=Decimal('10.01')))
        lid,_=self.s['rentals'].save_lease(Lease(aid,'Tenant',self.day,rent_amount=Decimal('0.1'),currency='USD'))
        self.s['rentals'].save_payment(RentPayment(lid,self.day,status='received',received_amount=Decimal('9.01'),received_currency='RUB',cb_rate=Decimal('90.01')))
        report=self.s['reports'].account(aid)[1]['rent']['payments'][0]
        self.assertEqual(report['fx_diff'],Decimal('0.009'))
        flow=self.vault.con.execute('SELECT amount FROM cashflows WHERE account_id=?',(aid,)).fetchone()[0]
        self.assertEqual(flow,Decimal('9.01'))


class DecimalMigrationTests(unittest.TestCase):
    def test_v9_real_migration_preserves_links_sequences_and_rolls_back(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        with patch('db._migrate_decimals'), patch('db._migrate_ledger'),patch('db._migrate_liabilities'),patch('db._migrate_tax_documents'),patch('db._migrate_goal_forecasts'),patch('db._migrate_instruments'),patch('db._migrate_macro_versions'),patch('db._migrate_macro_calculations'),patch('db._migrate_economic_cycle'),patch('db._migrate_market_catalog'),patch('db._migrate_sector_scoring'):db.init_db(con)
        con.execute("INSERT INTO entities(id,entity_type,name) VALUES (7,'account','Old')")
        con.execute("INSERT INTO financial_accounts(id,kind) VALUES (7,'broker')")
        con.execute("INSERT INTO holdings(id,account_id,name,quantity,price,avg_price) VALUES (123,7,'Old',?,?,?)",(0.1+0.2,1.25,0.1))
        con.execute("INSERT INTO cashflows(id,account_id,date,type,amount,currency) VALUES (222,7,'2020-01-01','deposit',0.1,'RUB')")
        con.commit();before=list(con.iterdump())
        original=db._migrate_decimals
        def failed(c):original(c);raise RuntimeError('migration interrupted')
        with patch('db._migrate_decimals',side_effect=failed):
            with self.assertRaises(RuntimeError):db.init_db(con)
        self.assertEqual(list(con.iterdump()),before)
        db.init_db(con)
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],db.SCHEMA_VERSION)
        row=con.execute('SELECT id,account_id,quantity,price FROM holdings').fetchone()
        self.assertEqual(tuple(row),(123,7,Decimal(str(0.1+0.2)),Decimal('1.25')))
        self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
        before=list(con.iterdump());db.init_db(con);self.assertEqual(list(con.iterdump()),before)
        new=con.execute("INSERT INTO holdings(account_id,name,quantity,price) VALUES (7,'New',?,?)",(Decimal('1e-18'),Decimal('0.2'))).lastrowid
        self.assertGreater(new,123)

    def test_encrypted_migration_failure_restores_v9_and_retry_succeeds(self):
        import tempfile
        from vault import Vault
        with tempfile.TemporaryDirectory() as directory:
            vault=Vault(str(Path(directory)/'migration.enc'))
            try:
                with patch('db._migrate_decimals'), patch('db._migrate_ledger'),patch('db._migrate_liabilities'),patch('db._migrate_tax_documents'),patch('db._migrate_goal_forecasts'),patch('db._migrate_instruments'),patch('db._migrate_macro_versions'),patch('db._migrate_macro_calculations'),patch('db._migrate_economic_cycle'),patch('db._migrate_market_catalog'),patch('db._migrate_sector_scoring'):db.init_db(vault.create('password123'))
                vault.save(force=True)
                before=list(vault.con.iterdump());disk=Path(vault.path).read_bytes()
                with patch('vault.os.replace',side_effect=OSError('full')):
                    with self.assertRaises(OSError):initialize_vault(vault)
                self.assertEqual(list(vault.con.iterdump()),before)
                self.assertEqual(Path(vault.path).read_bytes(),disk)
                initialize_vault(vault)
                vault.lock();vault.unlock('password123')
                self.assertEqual(vault.con.execute('PRAGMA user_version').fetchone()[0],db.SCHEMA_VERSION)
                self.assertIsInstance(vault.con.execute("SELECT rate FROM rates WHERE currency='USD'").fetchone()[0],Decimal)
            finally:vault.lock()
