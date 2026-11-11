"""Stable codes, exact preservation of custom text, and reversible v6 migration."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import db
from vault import Vault
from wealth_manager.bootstrap import build_services
from wealth_manager.factory import create_app
from wealth_manager.adapters.persistence.migrations import initialize_vault
from wealth_manager.adapters.web.i18n import EN, classification_label
from wealth_manager.shared.classifications import ASSET_LABELS, CATEGORY_LABELS, asset_key, category_key
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.vault=Vault(str(Path(self.tmp.name)/'vault.enc'));con=self.vault.create('password123')
        self.addCleanup(self.vault.lock)
        db._init_legacy(con)
        con.execute("INSERT INTO accounts(id,name,kind) VALUES (1,'My account','broker')")
        classes=list(db.ASSET_CODES_V7)+['cash','custom:gold','Свои активы','Деньги ','<img src=x onerror=alert(1)>']
        for i,label in enumerate(classes,1):
            con.execute('INSERT INTO holdings(id,account_id,name,asset_class,quantity,avg_price,price) VALUES (?,1,?,?,2,3,4)',(i,'Name '+label,label))
        categories=list(db.CATEGORY_CODES_V7)+['tax','Cash','Моя категория','Аренда ','']
        for i,label in enumerate(categories,1):
            con.execute("INSERT INTO cashflows(id,account_id,date,type,amount,currency,category,source,note) VALUES (?,1,'2026-01-01','income',10,'RUB',?,'Original source','Original note')",(i,label))
        con.commit();db._migrate_entities(con);db._migrate_planning(con);db._migrate_research(con)
        con.execute("INSERT INTO goals(id,name,target_amount,currency) VALUES (1,'Goal','100','RUB')")
        con.execute("INSERT INTO portfolios(id,name,goal_id) VALUES (1,'Portfolio',1)")
        con.execute("INSERT INTO allocations(portfolio_id,holding_id,share_bps) VALUES (1,1,10000)")
        con.commit();self.vault.save(force=True)
        self.s=build_services(self.vault,today=lambda:date(2026,11,11))
    def rows(self,table):return [dict(r) for r in self.vault.con.execute('SELECT * FROM '+table+' ORDER BY id')]
    def dump(self):return list(self.vault.con.iterdump())

    def test_migration_maps_all_builtins_preserves_custom_text_and_financial_reports(self):
        before=self.s['reports'].dashboard();holdings=self.rows('holdings');flows=self.rows('cashflows')
        links=self.rows('allocations')
        initialize_vault(self.vault)
        for old,new in zip(holdings,self.rows('holdings')):
            code=db.ASSET_CODES_V7.get(old['asset_class'],'custom')
            self.assertEqual(new,dict(old,asset_class='' if code!='custom' else old['asset_class'],asset_class_code=code))
        for old,new in zip(flows,self.rows('cashflows')):
            code=db.CATEGORY_CODES_V7.get(old['category'],'')
            self.assertEqual(new,dict(old,category='' if code else old['category'],category_code=code))
        # Dashboard embeds recent records, whose representation intentionally changes.
        after=self.s['reports'].dashboard()
        for key in ('total','by_class','by_cur','hist','inc'):
            if key=='inc':
                self.assertEqual(after[key]['totals'],before[key]['totals'])
                self.assertEqual(after[key]['by_cat'],before[key]['by_cat'])
            else:self.assertEqual(after[key],before[key])
        self.assertEqual(self.rows('allocations'),links)
        self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(),[])
        snapshot=self.dump();db.init_db(self.vault.con);self.assertEqual(self.dump(),snapshot)
        self.vault.con.execute("INSERT INTO holdings(account_id,name) VALUES (1,'Default')");self.vault.con.commit()
        self.assertEqual(self.rows('holdings')[-1]['asset_class_code'],'stocks')
        self.assertEqual(self.rows('holdings')[-1]['asset_class'],'')

    def test_migration_failure_restores_schema_file_and_can_retry(self):
        before=self.dump();encrypted=Path(self.vault.path).read_bytes()
        with patch('db.ASSET_CODES_V7',{'Акции':None}):
            with self.assertRaises(sqlite3.IntegrityError):db.init_db(self.vault.con)
        self.assertEqual(self.dump(),before)
        with patch('vault.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):initialize_vault(self.vault)
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)
        self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],6)
        initialize_vault(self.vault)
        self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],db.SCHEMA_VERSION)

    def test_codes_custom_collisions_cash_behavior_and_validation(self):
        initialize_vault(self.vault)
        cash=self.s['positions'].save(Position(1,asset_class_code='cash',quantity=25,price=99,avg_price=10))
        row=next(r for r in self.rows('holdings') if r['id']==cash)
        self.assertEqual((row['price'],row['avg_price'],row['asset_class']),(1,1,''))
        custom=self.s['positions'].save(Position(1,asset_class_code='custom',asset_class='cash',quantity=25,price=99))
        row=next(r for r in self.rows('holdings') if r['id']==custom)
        self.assertEqual((row['price'],asset_key(row)),(99,'custom:cash'))
        self.s['positions'].save(Position(1,id=custom,asset_class_code='custom',asset_class='Деньги',price=99))
        self.assertEqual(next(r for r in self.rows('holdings') if r['id']==custom)['asset_class'],'Деньги')
        for record in (Position(1,asset_class_code='bad'),Position(1,asset_class_code='custom')):
            with self.assertRaises(ValueError):self.s['positions'].save(record)
        with self.assertRaises(ValueError):self.s['cashflows'].save(Cashflow(1,'2026-01-01',category_code='bad'))
        self.assertEqual(category_key(dict(category='Unknown')),'custom:Unknown')
        self.assertEqual(asset_key(dict(asset_class='Unknown')),'custom:Unknown')

    def test_web_ru_en_custom_edit_legacy_forms_export_and_no_translation_of_user_text(self):
        initialize_vault(self.vault);app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as session:session['vault']=self.vault.token
        for language,label in [('ru','Деньги'),('en','Cash')]:
            self.s['change_language'].execute(language)
            before=self.dump()
            page=client.get('/accounts/1').get_data(as_text=True)
            self.assertIn('value="cash">'+label+'</option>',page)
            self.assertIn('Свои активы',page)
            self.assertIn('&lt;img src=x onerror=alert(1)&gt;',page)
            self.assertIn('Моя категория',client.get('/operations').get_data(as_text=True))
            exported=client.get('/export.csv').get_data(as_text=True)
            self.assertIn(label,exported);self.assertIn('Моя категория',exported)
            self.assertEqual(self.dump(),before)
        # Explicit custom category survives even when it equals a legacy built-in label.
        r=client.post('/flows/save',data=dict(account_id=1,type='income',date='2026-01-01',amount=10,category_code='',category='Аренда'))
        self.assertEqual(r.status_code,302)
        row=self.rows('cashflows')[-1];self.assertEqual((row['category'],row['category_code']),('Аренда',''))
        self.assertEqual(client.post('/flows/save',data=dict(id=row['id'],account_id=1,type='income',date='2026-01-01',amount=11,category_code='',category='Аренда')).status_code,302)
        self.assertEqual(self.rows('cashflows')[-1]['category'],'Аренда')
        # Old forms without code fields remain accepted.
        client.post('/flows/save',data=dict(account_id=1,type='income',date='2026-01-01',amount=10,category='Аренда'))
        self.assertEqual(self.rows('cashflows')[-1]['category_code'],'rent')
        client.post('/holdings/save',data=dict(account_id=1,asset_class='Деньги',quantity=1,price=10))
        self.assertEqual(self.rows('holdings')[-1]['asset_class_code'],'cash')
        client.post('/holdings/save',data=dict(account_id=1,asset_class_code='bonds',asset_class='stale text',quantity=1,price=10))
        self.assertEqual(self.rows('holdings')[-1]['asset_class_code'],'bonds')
        self.assertEqual(self.rows('holdings')[-1]['asset_class'],'')
        client.post('/flows/save',data=dict(account_id=1,type='income',date='2026-01-01',amount=10,category_code='dividends',category='stale'))
        self.assertEqual((self.rows('cashflows')[-1]['category_code'],self.rows('cashflows')[-1]['category']),('dividends',''))
        with app.test_request_context():
            self.assertEqual(classification_label('custom:Деньги'),'Деньги')
            self.assertEqual(classification_label('unknown'),'unknown')
        for label in list(ASSET_LABELS.values())+list(CATEGORY_LABELS.values()):self.assertIn(label,EN)

    def test_sql_dump_roundtrip_keeps_codes_text_and_allocation_constraints(self):
        initialize_vault(self.vault);before=self.dump();con=self.vault.con
        class DumpOnly:
            def __getattr__(self,name):
                if name=='serialize':raise AttributeError(name)
                return getattr(con,name)
        self.vault.con=DumpOnly();self.vault.save(force=True);self.vault.lock();self.vault.unlock('password123')
        db.init_db(self.vault.con);self.assertEqual(self.dump(),before)
        with self.assertRaises(sqlite3.IntegrityError):
            self.vault.con.execute('INSERT INTO allocations(portfolio_id,holding_id,share_bps) VALUES (1,1,1)')
        self.vault.con.rollback()
