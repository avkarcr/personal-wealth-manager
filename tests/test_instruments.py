"""Business stage 9: explicit identity without inventing or duplicating wealth."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import json
import sqlite3
import unittest
from unittest.mock import patch
from vault_fixtures import PortfolioCase, historical_schema
import db
from wealth_manager.modules.companies.domain import Company
from wealth_manager.modules.instruments.domain import CryptoAsset, compatible
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.planning.domain import Portfolio
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.adapters.persistence.instruments import Repository


class InstrumentTests(PortfolioCase):
    def company(self):return self.s['companies'].save(Company('Example','SAME','MOEX'))
    def crypto(self):return self.s['instruments'].save_crypto(CryptoAsset('Token','SAME','Ethereum','0xAbC'))
    def detail(self,cid):return self.s['instrument_reports'].detail('company',cid)

    def test_explicit_link_no_ticker_inference_no_capital_mutation_durable(self):
        cid=self.company();before=self.s['reports'].dashboard()
        self.assertEqual(self.detail(cid)['positions'],[])
        self.assertFalse(self.detail(cid)['owned'])
        self.s['instruments'].link(self.hid,'company',cid)
        self.s['instruments'].link(self.hid,'company',cid)
        self.assertEqual(self.s['reports'].dashboard(),before)
        self.assertTrue(self.detail(cid)['owned']);self.assertEqual(self.detail(cid)['totals']['RUB']['known'],100)
        self.assertEqual(len(self.detail(cid)['positions']),1)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.s['instrument_reports'].links()[self.hid]['id'],cid)
        self.s['companies'].save(Company('Example','SAME','MOEX',status='archived',id=cid))
        self.assertTrue(self.detail(cid)['owned'])

    def test_portfolio_shares_and_currency_totals_not_double_counted(self):
        cid=self.company();pid2=self.s['planning'].save_portfolio(Portfolio('Second',self.gid))
        self.s['planning'].allocate(self.pid,'holding',self.hid,60)
        self.s['planning'].allocate(pid2,'holding',self.hid,40)
        usd=self.s['positions'].save(Position(self.aid,name='USD stock',currency='USD',quantity=2,price=10))
        for hid in (self.hid,usd):self.s['instruments'].link(hid,'company',cid)
        detail=self.detail(cid)
        self.assertEqual(detail['totals'],{'RUB':{'known':D(100),'complete':True},'USD':{'known':D(20),'complete':True}})
        rub=next(p for p in detail['positions'] if p['id']==self.hid)
        self.assertEqual([a['value'] for a in rub['allocations']],[60,40])
        self.assertEqual(sum(a['percent'] for a in rub['allocations']),100)
        self.assertEqual(self.s['planning_reports'].goal_detail(self.gid)['goal']['total'],100)

    def test_missing_quotes_fx_and_stale_dates_preserved(self):
        cid=self.company();self.s['instruments'].link(self.hid,'company',cid)
        self.s['planning'].allocate(self.pid,'holding',self.hid,100)
        self.s['positions'].update_prices(self.aid,{self.hid:D(0)})
        data=self.detail(cid);self.assertFalse(data['totals']['RUB']['complete'])
        self.assertIsNone(data['positions'][0]['allocations'][0]['value'])
        self.assertIn('missing',[q['status'] for q in data['valuation_quality']])
        old=self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(self.hid,)).fetchone()
        self.s['positions'].save(replace(Position(**dict(old)),price=D(25),currency='ZZZ'))
        self.assertEqual(self.detail(cid)['totals']['ZZZ']['known'],50)
        self.assertIsNone(self.detail(cid)['positions'][0]['allocations'][0]['value'])
        self.vault.con.execute("UPDATE holdings SET updated_at='2000-01-01' WHERE id=?",(self.hid,));self.vault.con.commit()
        self.assertIn('stale',[q['status'] for q in self.detail(cid)['valuation_quality']])

    def test_compatible_types_no_reassignment_and_linked_identity_guard(self):
        cid=self.company();crypto=self.crypto()
        ch=self.s['positions'].save(Position(self.aid,name='Coin',asset_class_code='crypto',quantity=1,price=5))
        cash=self.s['positions'].save(Position(self.aid,name='Cash',asset_class_code='cash'))
        for hid,kind,rid in ((ch,'company',cid),(self.hid,'crypto',crypto),(cash,'company',cid),(self.hid,'bad',cid)):
            with self.assertRaises(RecordError):self.s['instruments'].link(hid,kind,rid)
        self.s['instruments'].link(ch,'crypto',crypto)
        self.s['instruments'].link(self.hid,'company',cid)
        other=self.s['companies'].save(Company('Other'))
        with self.assertRaises(RecordError):self.s['instruments'].link(self.hid,'company',other)
        old=Position(**dict(self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(self.hid,)).fetchone()))
        for change in (dict(asset_class_code='crypto'),dict(ticker='OTHER')):
            with self.assertRaises(RecordError):self.s['positions'].save(replace(old,**change))
        self.s['positions'].save(replace(old,quantity=D(3),price=D(60)))
        self.assertEqual(self.detail(cid)['totals']['RUB']['known'],180)
        self.s['instruments'].unlink(self.hid)
        self.s['instruments'].link(self.hid,'company',other)
        self.assertEqual(self.detail(cid)['positions'],[])
        self.assertEqual(self.s['instrument_reports'].detail('crypto',crypto)['totals']['RUB']['known'],5)

    def test_archive_closed_accounts_zero_positions_and_delete_rules(self):
        cid=self.company();crypto=self.crypto()
        self.s['instruments'].link(self.hid,'company',cid)
        with self.assertRaises(RecordError):self.s['companies'].delete(cid)
        self.s['positions'].save(Position(self.aid,name='Zero',id=self.hid,quantity=0))
        self.assertFalse(self.detail(cid)['owned'])
        self.s['accounts'].close(self.aid)
        self.assertEqual(self.detail(cid)['totals']['RUB']['known'],0)
        self.assertFalse(self.detail(cid)['positions'][0]['active'])
        self.s['instruments'].unlink(self.hid)
        with self.assertRaises(RecordError):self.s['instruments'].link(self.hid,'company',cid)
        self.s['accounts'].reopen(self.aid)
        self.s['companies'].save(Company('Example','SAME','MOEX',id=cid,status='archived'))
        with self.assertRaises(RecordError):self.s['instruments'].link(self.hid,'company',cid)
        ch=self.s['positions'].save(Position(self.aid,name='Coin',asset_class_code='stablecoins'))
        self.s['instruments'].link(ch,'crypto',crypto)
        with self.assertRaises(RecordError):self.s['instruments'].delete_crypto(crypto)
        self.s['instruments'].save_crypto(CryptoAsset('Token','SAME','Ethereum','0xAbC',id=crypto,archived=True))
        self.assertEqual(len(self.s['instrument_reports'].detail('crypto',crypto)['positions']),1)
        self.s['positions'].delete(ch)
        self.assertNotIn(ch,self.s['instrument_reports'].links())
        self.s['instruments'].delete_crypto(crypto);self.s['companies'].delete(cid)

    def test_crypto_identity_validation_and_unknown_ids(self):
        cid=self.crypto()
        with self.assertRaises(RecordError):self.s['instruments'].save_crypto(CryptoAsset('Duplicate','same','ethereum','0xAbC'))
        for record in (CryptoAsset('','BTC'),CryptoAsset('Bitcoin',''),CryptoAsset('Token','A',contract='address'),CryptoAsset('Bad','BAD',website='javascript:alert(1)')):
            with self.assertRaises(RecordError):self.s['instruments'].save_crypto(record)
        self.s['instruments'].save_crypto(CryptoAsset('Other network','SAME','Solana','0xAbC'))
        self.assertEqual(len(self.s['instrument_reports'].overview()['cryptos']),2)
        for action in (lambda:self.s['instruments'].link(999,'company',1),lambda:self.s['instruments'].unlink(999),lambda:self.s['instruments'].link(self.hid,'company',999),lambda:self.s['instruments'].delete_crypto(999),lambda:self.s['instrument_reports'].detail('bad',1)):
            with self.assertRaises(EntityNotFound):action()
        with self.assertRaises(RecordError):Repository(self.vault.con).target('bad',1)
        self.assertTrue(compatible('company','bonds'));self.assertFalse(compatible('company','funds'))
        self.s['instruments'].save_crypto(CryptoAsset('Updated','SAME','Ethereum','0xAbC',id=cid))

    def test_encrypted_save_failure_rolls_back_link_and_crypto(self):
        cid=self.company();before=self.dump();disk=Path(self.vault.path).read_bytes()
        for action in (lambda:self.s['instruments'].link(self.hid,'company',cid),self.crypto):
            with patch('vault.os.replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):action()
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)

    def test_http_locales_links_read_only_and_escaping(self):
        cid=self.s['companies'].save(Company('<script>bad</script>'))
        response=self.client.post('/crypto-assets/save',data=dict(name='Token',symbol='TOK',network='Chain',contract='<script>bad</script>'))
        self.assertEqual(response.status_code,302);crypto=int(response.location.rsplit('/',1)[1])
        ch=self.s['positions'].save(Position(self.aid,name='Coin',asset_class_code='crypto'))
        for hid,kind,rid in ((self.hid,'company',cid),(ch,'crypto',crypto)):
            self.assertEqual(self.client.post(f'/instruments/{kind}/{rid}/link',data=dict(holding_id=hid)).status_code,302)
        self.s['planning'].allocate(self.pid,'holding',self.hid,100)
        for lang,label in [('ru','Связанные позиции'),('en','Linked positions')]:
            self.s['change_language'].execute(lang);before=self.dump();disk=Path(self.vault.path).read_bytes()
            for path in ('/companies','/crypto-assets',f'/companies/{cid}',f'/crypto-assets/{crypto}',f'/accounts/{self.aid}',f'/portfolios/{self.pid}'):
                r=self.client.get(path);self.assertEqual(r.status_code,200,path)
                self.assertNotIn('<script>bad</script>',r.get_data(as_text=True))
            self.assertIn(label,self.client.get(f'/companies/{cid}').get_data(as_text=True))
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        self.assertEqual(self.client.post(f'/crypto-assets/{crypto}/delete').status_code,400)
        response=self.client.post(f'/positions/{ch}/unlink');self.assertTrue(response.location.endswith(f'/crypto-assets/{crypto}'))
        self.assertEqual(self.client.post(f'/positions/{ch}/unlink').status_code,302)
        self.assertEqual(self.client.post(f'/crypto-assets/{crypto}/delete').status_code,302)
        self.assertEqual(self.client.get('/crypto-assets/999').status_code,404)
        self.assertEqual(self.client.get('/companies/999').status_code,404)
        self.assertEqual(self.client.post('/crypto-assets/save',data={}).status_code,400)
        self.assertEqual(self.client.post('/instruments/company/1/link',data=dict(holding_id='bad')).status_code,400)
        self.assertEqual(self.client.get(f'/positions/{self.hid}/unlink').status_code,405)
        self.client.post('/lock')
        self.assertEqual(self.client.post('/crypto-assets/save',data=dict(name='X',symbol='X')).status_code,302)

    def test_ledger_sales_cancellation_and_transfer_identity(self):
        from wealth_manager.modules.ledger.domain import Entry
        from wealth_manager.modules.accounts.domain import Account
        self.s['positions'].save(Position(self.aid,name='Stock',ticker='SAME',quantity=2,price=50,avg_price=40,id=self.hid))
        cid=self.company();self.s['instruments'].link(self.hid,'company',cid)
        cash=self.s['positions'].save(Position(self.aid,asset_class_code='cash',quantity=1000))
        self.s['ledger'].activate(self.aid)
        sold=self.s['ledger'].post(Entry(self.day,'sell',self.hid,2,50,cash_id=cash,request_id='sell-all'))
        self.assertFalse(self.detail(cid)['owned']);self.assertEqual(self.detail(cid)['totals']['RUB']['known'],0)
        self.s['ledger'].cancel(sold)
        self.assertTrue(self.detail(cid)['owned']);self.assertEqual(self.detail(cid)['totals']['RUB']['known'],100)
        aid2=self.s['save_financial_account'].execute(Account('Second broker'))
        target=self.s['positions'].save(Position(aid2,name='Same ticker',ticker='SAME',quantity=0,price=50))
        self.s['ledger'].activate(aid2)
        transfer=Entry(self.day,'transfer',self.hid,1,50,target_id=target,request_id='transfer-one')
        before=self.dump()
        with self.assertRaises(RecordError):self.s['ledger'].post(transfer)
        self.assertEqual(self.dump(),before)
        other=self.s['companies'].save(Company('Different issuer','SAME','OTHER'))
        self.s['instruments'].link(target,'company',other)
        with self.assertRaises(RecordError):self.s['ledger'].post(transfer)
        self.s['instruments'].unlink(target);self.s['instruments'].link(target,'company',cid)
        self.s['ledger'].post(transfer)
        self.assertEqual(len(self.detail(cid)['positions']),2)
        self.assertEqual(self.detail(cid)['totals']['RUB']['known'],100)


class InstrumentMigrationTests(unittest.TestCase):
    def test_v14_upgrade_preserves_data_no_automatic_links_and_rolls_back(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        historical_schema(c, 14)
        c.execute("INSERT INTO companies(name,ticker,exchange,country,status) VALUES ('Old','OLD','MOEX','RU','watch')");c.commit()
        before=list(c.iterdump());original=db._migrate_instruments
        def fail(con):original(con);raise RuntimeError('migration')
        with patch('db._migrate_instruments',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(c)
        self.assertEqual(list(c.iterdump()),before)
        self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],14)
        db.init_db(c)
        self.assertEqual(c.execute('SELECT name FROM companies').fetchone()[0],'Old')
        self.assertEqual(c.execute('SELECT count(*) FROM holding_links').fetchone()[0],0)
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        before=list(c.iterdump());db.init_db(c);self.assertEqual(list(c.iterdump()),before)
