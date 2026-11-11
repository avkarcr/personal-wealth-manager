"""Stage 6: isolated domains, CRUD, migration, encrypted durability and RU/EN."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import db
from vault import Vault
from wealth_manager.bootstrap import build_services
from wealth_manager.factory import create_app
from wealth_manager.modules.companies.domain import Company
from wealth_manager.modules.macroeconomics.domain import Observation
from wealth_manager.modules.taxes.domain import TaxEntry, Deduction, estimate, summarize as tax_summary
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.adapters.persistence.research import CompanyReader, ObservationReader, TaxReader

D=Decimal

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.vault=Vault(str(Path(self.tmp.name)/'test.enc'))
        db.init_db(self.vault.create('password123'));self.vault.save(force=True);self.addCleanup(self.vault.lock)
        self.s=build_services(self.vault)
        self.company=Company('Example','EXM','MOEX','RU',website='https://example.com',thesis='Long term')
        self.observation=Observation('RU','key_rate','2026-01-01',D('10.5'),'Manual source')
        self.entry=TaxEntry(2026,'RU','Salary','RUB',D('100000'),D('13'),D('5000'))
        self.deduction=Deduction(2026,'RU','Education','RUB',D('10000'),D('1300'),documents='Invoice 123; home folder')
    def dump(self):return list(self.vault.con.iterdump())
    def seed(self):
        return (self.s['companies'].save(self.company),self.s['macroeconomics'].save(self.observation),
                self.s['taxes'].save_entry(self.entry),self.s['taxes'].save_deduction(self.deduction))

    def test_crud_reopen_and_no_changes_to_wealth_or_cashflows(self):
        before=self.s['reports'].dashboard()
        ids=self.seed()
        self.s['companies'].save(replace(self.company,id=ids[0],name='Renamed',status='owned'))
        self.s['macroeconomics'].save(replace(self.observation,id=ids[1],value=D('11')))
        self.s['taxes'].save_entry(replace(self.entry,id=ids[2],paid=D('13000')))
        self.s['taxes'].save_deduction(replace(self.deduction,id=ids[3],status='received'))
        self.assertEqual(self.s['reports'].dashboard(),before)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.s['company_reports'].overview()['companies'][0]['name'],'Renamed')
        self.assertEqual(self.s['macro_reports'].overview()['observations'][0]['value'],11)
        summary=self.s['tax_reports'].overview()['summary'][0]
        self.assertEqual((summary['tax'],summary['balance'],summary['refund_received']),(13000,0,1300))
        for action,rid in zip((self.s['companies'].delete,self.s['macroeconomics'].delete,self.s['taxes'].delete_entry,self.s['taxes'].delete_deduction),ids):
            action(rid)
            with self.assertRaises(EntityNotFound):action(rid)
        self.assertEqual(self.s['tax_reports'].overview()['summary'],[])
        self.assertEqual(self.s['company_reports'].overview()['companies'],[])
        self.assertEqual(self.s['macro_reports'].overview()['summary'],[])

    def test_duplicate_company_listing_and_observation_are_not_overwritten(self):
        ids=self.seed();before=self.dump()
        with self.assertRaises(RecordError):self.s['companies'].save(replace(self.company,ticker='exm',exchange='moex'))
        with self.assertRaises(RecordError):self.s['macroeconomics'].save(self.observation)
        self.assertEqual(self.dump(),before)
        self.s['companies'].save(replace(self.company,exchange='OTHER'))
        self.s['companies'].save(Company('Private company'))
        self.s['companies'].save(Company('Another private company'))
        self.s['macroeconomics'].save(replace(self.observation,country='US'))
        for command,record in [(self.s['companies'].save,self.company),(self.s['macroeconomics'].save,self.observation),
                               (self.s['taxes'].save_entry,self.entry),(self.s['taxes'].save_deduction,self.deduction)]:
            # Use changed unique keys to reach the missing-ID check.
            if isinstance(record,Company):record=replace(record,ticker='MISSING')
            if isinstance(record,Observation):record=replace(record,date='2026-02-01')
            with self.assertRaises(EntityNotFound):command(replace(record,id=999))
        self.assertEqual(self.s['tax_reports'].overview()['summary'][0]['balance'],8000)

    def test_macro_history_order_and_country_indicator_isolation(self):
        self.s['macroeconomics'].save(self.observation)
        self.s['macroeconomics'].save(replace(self.observation,date='2025-12-01',value=D('12')))
        self.s['macroeconomics'].save(replace(self.observation,country='US',value=D('4')))
        self.s['macroeconomics'].save(replace(self.observation,indicator='gdp_yoy',value=D('-1.2')))
        report=self.s['macro_reports'].overview()
        rate=next(r for r in report['summary'] if r['country']=='RU' and r['indicator']=='key_rate')
        self.assertEqual((rate['latest']['value'],rate['change']),(D('10.5'),D('-1.5')))
        self.assertEqual(rate['previous']['date'],'2025-12-01')
        self.assertIsNone(next(r for r in report['summary'] if r['country']=='US')['change'])

    def test_taxes_do_not_mix_currencies_years_countries_or_pending_refunds(self):
        self.seed()
        for change in ({'currency':'USD'},{'country':'KZ'},{'year':2025}):self.s['taxes'].save_entry(replace(self.entry,**change))
        self.s['taxes'].save_deduction(replace(self.deduction,status='received',refund=D('500')))
        self.s['taxes'].save_deduction(replace(self.deduction,status='rejected',refund=D('900')))
        self.s['taxes'].save_deduction(replace(self.deduction,status='submitted',refund=D('100')))
        summary=self.s['tax_reports'].overview()['summary'];self.assertEqual(len(summary),4)
        row=next(r for r in summary if (r['year'],r['country'],r['currency'])==(2026,'RU','RUB'))
        self.assertEqual((row['tax'],row['paid'],row['balance'],row['refund_expected'],row['refund_received']),(13000,5000,8000,1400,500))
        self.assertEqual(estimate(dict(base='0.05',rate='10')),D('0.01'))
        self.assertEqual(estimate(dict(base='100.01',rate='13')),D('13.00'))
        self.assertEqual(tax_summary([dict(year=2026,country='RU',currency='RUB',base='1',rate='0',paid='2')],[])[0]['balance'],-2)

    def test_invalid_domain_inputs_do_not_open_uow_or_modify_database(self):
        invalid=[replace(self.company,name=' '),replace(self.company,country='Russia'),replace(self.company,country='ru'),
                 replace(self.company,ticker=''),replace(self.company,status='bad'),replace(self.company,website='javascript:alert(1)'),
                 replace(self.company,website='https://user:pass@example.com'),replace(self.company,website='https://[bad'),
                 replace(self.observation,date='not-a-date'),replace(self.observation,indicator='unknown'),replace(self.observation,value=D('NaN')),
                 replace(self.observation,source=''),replace(self.entry,year=0),replace(self.entry,currency='rUB'),
                 replace(self.entry,base=D('-1')),replace(self.entry,base=D('1e30')),replace(self.observation,date='20260101'),replace(self.entry,rate=D('101')),replace(self.entry,paid=D('Infinity')),
                 replace(self.deduction,amount=D('-1')),replace(self.deduction,status='bad'),replace(self.deduction,category='')]
        before=self.dump()
        for record in invalid:
            with self.subTest(record=record),self.assertRaises(RecordError):record.validate()
        self.assertEqual(self.dump(),before)
        from wealth_manager.modules.companies.application import Commands
        with self.assertRaises(RecordError):Commands(lambda:self.fail('Must validate before UoW')).save(invalid[0])

    def test_every_mutation_restores_memory_and_ciphertext_after_disk_failure(self):
        c,m,t,d=self.seed()
        actions=[lambda:self.s['companies'].save(replace(self.company,id=c,name='Changed')),
                 lambda:self.s['macroeconomics'].save(replace(self.observation,id=m,value=D(1))),
                 lambda:self.s['taxes'].save_entry(replace(self.entry,id=t,paid=D(1))),
                 lambda:self.s['taxes'].save_deduction(replace(self.deduction,id=d,status='ready')),
                 lambda:self.s['companies'].delete(c),lambda:self.s['macroeconomics'].delete(m),
                 lambda:self.s['taxes'].delete_entry(t),lambda:self.s['taxes'].delete_deduction(d)]
        for action in actions:
            before=self.dump();encrypted=Path(self.vault.path).read_bytes()
            with patch('vault.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):action()
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)
            self.assertFalse(self.vault.dirty())

    def test_populated_sql_dump_roundtrip_and_locked_readers(self):
        self.seed();before=self.dump();connection=self.vault.con
        class DumpOnly:
            def __getattr__(self,name):
                if name=='serialize':raise AttributeError(name)
                return getattr(connection,name)
        self.vault.con=DumpOnly();self.vault.save(force=True);self.vault.lock();self.vault.unlock('password123')
        db.init_db(self.vault.con);self.assertEqual(self.dump(),before)
        self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],db.SCHEMA_VERSION)
        self.vault.lock()
        for reader in (CompanyReader(self.vault),ObservationReader(self.vault),TaxReader(self.vault)):
            with self.assertRaises(RuntimeError):reader.read()

    def test_http_forms_localization_validation_escaping_and_read_only_pages(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as session:session['vault']=self.vault.token
        forms=[('/companies/save',dict(name='<script>company</script>',ticker='exm',exchange='moex',country='ru',status='watch',website='https://example.com')),
               ('/macroeconomics/save',dict(country='ru',indicator='key_rate',date='2026-01-01',value='10,5',source='<script>source</script>')),
               ('/taxes/entries/save',dict(year=2026,country='ru',category='Salary',currency='rub',base='1000',rate='13',paid='0')),
               ('/taxes/deductions/save',dict(year=2026,country='ru',category='Education',currency='rub',amount='100',refund='13',status='collecting',documents='<script>document</script>'))]
        for url,data in forms:
            self.assertEqual(client.post(url,data=data).status_code,302)
            self.assertEqual(client.post(url,data=dict(data,id=1)).status_code,302)
        for lang in ('ru','en'):
            self.s['change_language'].execute(lang);before=self.dump()
            for url in ('/companies','/macroeconomics','/taxes'):
                response=client.get(url);self.assertEqual(response.status_code,200,url)
                page=response.get_data(as_text=True)
                self.assertNotRegex(page,r'<script>(company|source|document)</script>')
                self.assertIn('value="2026"',page) if url=='/taxes' else None
            self.assertEqual(self.dump(),before)
        self.assertIn('Macroeconomics',client.get('/macroeconomics').get_data(as_text=True))
        page=client.get('/').get_data(as_text=True)
        self.assertLess(page.index('href="/macroeconomics"'),page.index('href="/portfolios"'))
        response=client.post('/companies/save',data=dict(forms[0][1],website='javascript:alert(1)'))
        self.assertEqual(response.status_code,400);self.assertIn('URL must start',response.get_data(as_text=True))
        for url,data in forms:
            self.assertEqual(client.post(url,data=dict(data,id=999)).status_code,400 if url in ('/companies/save','/macroeconomics/save') else 404)
        for url in ('/companies/1/delete','/macroeconomics/1/delete','/taxes/entries/1/delete','/taxes/deductions/1/delete'):
            self.assertEqual(client.get(url).status_code,405)
            self.assertEqual(client.post(url).status_code,302)
            self.assertEqual(client.post(url).status_code,404)
        for url,data in forms:
            self.assertEqual(client.post(url,data={}).status_code,400)
        for index,key in ((1,'value'),(2,'base'),(2,'rate'),(3,'amount')):
            url,data=forms[index]
            self.assertEqual(client.post(url,data={k:v for k,v in data.items() if k!=key}).status_code,400)
        client.post('/lock')
        for url,data in forms:self.assertEqual(client.post(url,data=data).status_code,302)

    def test_dynamic_labels_and_errors_have_translations(self):
        from wealth_manager.adapters.web.i18n import EN
        for directory in ('companies','macroeconomics','taxes'):
            path=Path('wealth_manager/modules')/directory/'domain.py'
            for key in re.findall(r"'([^'\n]*[А-Яа-яЁё][^'\n]*)'",path.read_text()):self.assertIn(key,EN)
        for path in [Path('wealth_manager/shared/records.py'),Path('wealth_manager/adapters/persistence/research.py')]+list(Path('templates').glob('*_form.html')):
            for key in re.findall(r"'([^'\n]*[А-Яа-яЁё][^'\n]*)'",path.read_text()):self.assertIn(key,EN)


class ResearchMigrationTests(unittest.TestCase):
    def test_v5_upgrade_preserves_records_and_failure_rolls_back_whole_chain(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        db._init_legacy(con);db._migrate_entities(con);db._migrate_planning(con)
        con.execute("INSERT INTO goals(name,target_amount,currency) VALUES ('Goal','100','RUB')")
        con.execute("INSERT INTO portfolios(name,goal_id) VALUES ('Portfolio',1)");con.commit()
        before=list(con.iterdump())
        with patch('db.RESEARCH_SCHEMA',db.RESEARCH_SCHEMA+' INVALID SQL;'):
            with self.assertRaises(sqlite3.OperationalError):db.init_db(con)
        self.assertEqual(list(con.iterdump()),before)
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],5)
        db.init_db(con)
        self.assertEqual(con.execute('SELECT goal_id FROM portfolios').fetchone()[0],1)
        for table in ('companies','macro_observations','tax_entries','tax_deductions'):
            self.assertEqual(con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        after=list(con.iterdump());db.init_db(con);self.assertEqual(after,list(con.iterdump()))
        self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
        fresh=sqlite3.connect(':memory:');fresh.row_factory=sqlite3.Row;self.addCleanup(fresh.close)
        with patch('db.RESEARCH_SCHEMA',db.RESEARCH_SCHEMA+' INVALID SQL;'):
            with self.assertRaises(sqlite3.OperationalError):db.init_db(fresh)
        self.assertEqual(fresh.execute('PRAGMA user_version').fetchone()[0],0)
        self.assertEqual(fresh.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[])
