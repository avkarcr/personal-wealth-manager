"""M9 acceptance: distinct issuers/issues/listings, safe migration and wealth invariants."""
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal as D
from pathlib import Path
import ast
import json
import re
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_valuation_quality as fixtures
from wealth_manager.modules.market_catalog.domain import Sector,Security,Listing,find,security_class
from wealth_manager.modules.companies.domain import Company
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.accounts.domain import Account
from wealth_manager.modules.ledger.domain import Entry
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.shared.records import RecordError
from wealth_manager.adapters.persistence.market_catalog import Reader
from wealth_manager.adapters.persistence.migrations import initialize_vault


class CatalogTests(unittest.TestCase):
    setUp=fixtures.ValuationIntegrationTests.setUp
    dump=fixtures.ValuationIntegrationTests.dump
    def command(self):return self.s['market_catalog']
    def data(self):return Reader(self.vault).read()
    def issuer(self,name='Issuer'):return self.s['companies'].save(Company(name))
    def issue(self,cid,kind='common_share',**kw):return self.command().save_security(Security(cid,kind,kind,**kw))
    def days(self,n):return (self.today-timedelta(days=n)).isoformat()
    def sector(self,code):return next(s for s in self.data()['sectors'] if s['code']==code)

    def test_multilevel_classification_primary_and_unique_company_counts(self):
        c=self.command();cid=self.issuer();finance=self.sector('finance')['id'];consumer=self.sector('consumer')['id']
        sub=c.save_sector(Sector('banks','Банки','Banks',finance));c.assign_sector(cid,sub,True,'Annual report',self.day)
        c.assign_sector(cid,finance,False);c.assign_sector(cid,consumer,True)
        members=self.data()['memberships'];self.assertEqual(sum(m['is_primary'] for m in members),1)
        self.assertEqual(next(m['sector_id'] for m in members if m['is_primary']),consumer)
        before=self.data()['memberships'];c.assign_sector(cid,consumer,True);self.assertEqual(before,self.data()['memberships'])
        overview=self.s['catalog_reports'].overview();root=find(overview['sectors'],finance)
        self.assertEqual(root['company_ids'],[cid]);self.assertEqual(find(overview['sectors'],sub)['path_en'],'Financials / Banks')
        self.assertEqual(overview['unclassified'],[])
        for sid in (sub,finance,consumer):c.remove_sector(cid,sid)
        c.delete_sector(sub);self.assertEqual(self.s['catalog_reports'].overview()['unclassified'][0]['id'],cid)

    def test_taxonomy_rejects_cycles_children_of_children_and_orphaning(self):
        c=self.command();root=self.sector('finance');sid=c.save_sector(Sector('banks','Banks',parent_id=root['id']))
        for record in (replace(Sector(**root),parent_id=root['id']),replace(Sector(**root),parent_id=sid),Sector('deep','Too deep',parent_id=sid)):
            with self.assertRaises(RecordError):c.save_sector(record)
        with self.assertRaises(RecordError):c.delete_sector(root['id'])
        with self.assertRaises(RecordError):c.save_sector(Sector('banks','Duplicate'))
        cid=self.issuer();c.assign_sector(cid,sid)
        with self.assertRaises(RecordError):c.delete_sector(sid)
        c.save_sector(replace(Sector(**root),archived=True))
        with self.assertRaises(RecordError):c.assign_sector(cid,sid)
        with self.assertRaises(RecordError):c.save_sector(Sector('new_child','New child',parent_id=root['id']))
        self.assertFalse(find(self.s['catalog_reports'].overview()['sectors'],sid)['available'])
        c.save_sector(replace(Sector(**root),name='Finance renamed'))
        self.assertTrue(find(self.s['catalog_reports'].overview()['sectors'],sid)['path'].startswith('Finance renamed'))

    def test_issuers_are_never_merged_by_name_or_multiple_issues(self):
        cid=self.issuer('Same name');other=self.issuer('Same name')
        stock=self.issue(cid,isin='RU0000000001',registration_number='REG-1');pref=self.issue(cid,'preferred_share')
        bond=self.issue(cid,'bond',registration_number='BOND-1');dr=self.issue(other,'depositary_receipt',underlying_id=stock)
        data=self.data();self.assertEqual(len(data['companies']),2);self.assertEqual(len(data['securities']),4)
        self.assertEqual(self.s['catalog_reports'].security(dr)['underlying']['id'],stock)
        self.assertEqual(self.s['catalog_reports'].security(stock)['receipts'][0]['id'],dr)
        for record in (Security(other,'Duplicate','bond',isin='RU0000000001'),Security(cid,'Duplicate','bond',registration_number='REG-1')):
            with self.assertRaises(RecordError):self.command().save_security(record)
        self.issue(other,'bond',registration_number='REG-1')
        with self.assertRaises(RecordError):self.command().save_security(Security(other,'Moved issuer','common_share',id=stock))
        with self.assertRaises(RecordError):self.command().save_security(Security(cid,'Bond','bond',id=stock))
        with self.assertRaises(RecordError):self.command().delete_security(stock)
        with self.assertRaises(RecordError):self.s['companies'].delete(cid)
        self.command().delete_security(dr);self.s['companies'].delete(other)
        self.s['companies'].delete(cid);self.assertEqual(self.data()['securities'],[])

    def test_listing_identity_periods_boards_and_no_ticker_overwrite(self):
        c=self.command();cid=self.issuer();sid=self.issue(cid);other=self.issue(cid,'preferred_share')
        listing=Listing(sid,'MOEX','SAME','TQBR',self.days(50),self.days(20))
        lid=c.save_listing(listing)
        with self.assertRaises(RecordError):c.save_listing(replace(listing,security_id=other,ticker='same',exchange='moex'))
        with self.assertRaises(RecordError):c.save_listing(replace(listing,valid_from=self.days(20),valid_to=''))
        c.save_listing(replace(listing,valid_from=self.days(19),valid_to=''))
        c.save_listing(replace(listing,board='TQTF'))
        with self.assertRaises(RecordError):c.save_listing(replace(listing,board=''))
        for change in (dict(ticker='OTHER'),dict(exchange='OTHER'),dict(board='OTHER'),dict(security_id=other)):
            with self.assertRaises(RecordError):c.save_listing(replace(listing,id=lid,**change))
        blank=c.save_listing(Listing(sid,'OTHER','OTHER'))
        c.save_listing(Listing(sid,'OTHER','OTHER','BOARD',id=blank))
        c.delete_listing(blank);self.assertFalse(any(l['id']==blank for l in self.data()['listings']))

    def test_ticker_change_and_status_history_are_atomic_and_do_not_rewrite_positions(self):
        cid=self.issuer();sid=self.issue(cid);c=self.command()
        old=Listing(sid,'MOEX','OLD','TQBR',self.days(60),status='active',as_of=self.days(20),source='Exchange notice')
        lid=c.save_listing(old);self.s['instruments'].link(self.hid,'security',sid)
        before=self.s['reports'].dashboard();position=dict(self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(self.hid,)).fetchone())
        new_id=c.rename_listing(lid,'NEW',self.days(5),'Ticker notice');data=self.data()
        self.assertEqual(find(data['listings'],lid)['valid_to'],self.days(6));new=Listing(**find(data['listings'],new_id))
        self.assertEqual(new.status,'unknown');self.assertEqual(new.security_id,sid)
        current=replace(new,status='suspended',as_of=self.day,source='Suspension notice');c.save_listing(current);c.save_listing(current)
        events=[e for e in self.data()['events'] if e['listing_id']==new_id];self.assertEqual(len(events),2)
        self.assertEqual(events[-1]['status'],'suspended')
        with self.assertRaises(RecordError):c.save_listing(replace(current,as_of=self.days(1)))
        with self.assertRaises(RecordError):c.delete_listing(new_id)
        for rid,ticker,when in ((lid,'NEXT',self.day),(new_id,'NEW',self.day),(new_id,'NEXT',self.days(6)),(new_id,'NEXT','0001-01-01')):
            with self.assertRaises(RecordError):c.rename_listing(rid,ticker,when)
        self.assertEqual(before,self.s['reports'].dashboard());self.assertEqual(position,dict(self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(self.hid,)).fetchone()))
        self.assertTrue(self.s['catalog_reports'].security(sid)['listings'][1]['ended'])

    def test_explicit_issue_links_preserve_wealth_portfolio_and_unclassified_legacy_links(self):
        cid=self.issuer();sid=self.issue(cid);pref=self.issue(cid,'preferred_share');bond=self.issue(cid,'bond')
        self.s['planning'].allocate(self.pid,'holding',self.hid,100);before=self.s['reports'].dashboard()
        self.s['instruments'].link(self.hid,'company',cid)
        self.assertIsNone(self.s['instrument_reports'].detail('company',cid)['positions'][0]['security'])
        self.s['instruments'].link(self.hid,'security',sid);self.s['instruments'].link(self.hid,'company',cid)
        detail=self.s['instrument_reports'].detail('security',sid)
        self.assertEqual(detail['totals']['RUB']['known'],100);self.assertEqual(detail['positions'][0]['allocations'][0]['value'],100)
        self.assertEqual(before,self.s['reports'].dashboard());self.assertEqual(self.s['instrument_reports'].links()[self.hid]['security_id'],sid)
        for kind,rid,issue in [('security',pref,None),('company',self.issuer('Other'),sid)]:
            with self.assertRaises(RecordError):self.s['instruments'].link(self.hid,kind,rid,issue)
        self.s['instruments'].unlink(self.hid)
        with self.assertRaises(RecordError):self.s['instruments'].link(self.hid,'security',bond)
        unknown=self.issue(cid,'unknown')
        with self.assertRaises(RecordError):self.s['instruments'].link(self.hid,'security',unknown)
        self.s['instruments'].link(self.hid,'company',cid)
        self.assertTrue(any(p['id']==self.hid for p in self.s['instrument_reports'].detail('security',pref)['choices']))
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertIsNone(self.s['instrument_reports'].links()[self.hid]['security_id'])
        self.assertEqual(self.s['reports'].dashboard(),before)

    def test_bound_identity_archive_and_closed_positions_are_preserved(self):
        c=self.command();cid=self.issuer();sid=self.issue(cid);self.s['instruments'].link(self.hid,'security',sid)
        row=Security(**find(self.data()['securities'],sid))
        for fields in (dict(kind='preferred_share'),dict(isin='RU0000000001'),dict(registration_number='NEW')):
            with self.assertRaises(RecordError):c.save_security(replace(row,**fields))
        c.save_security(replace(row,name='Renamed',archived=True))
        self.assertEqual(self.s['instrument_reports'].detail('security',sid)['totals']['RUB']['known'],100)
        with self.assertRaises(RecordError):c.delete_security(sid)
        with self.assertRaises(RecordError):c.save_listing(Listing(sid,'MOEX','NEW'))
        fresh=self.s['positions'].save(Position(self.aid,name='New stock'))
        with self.assertRaises(RecordError):self.s['instruments'].link(fresh,'security',sid)
        position=Position(**dict(self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(self.hid,)).fetchone()))
        self.s['positions'].save(replace(position,quantity=0))
        self.s['accounts'].close(self.aid)
        self.assertEqual(self.s['instrument_reports'].detail('security',sid)['totals']['RUB']['known'],0)
        with self.assertRaises(RecordError):c.delete_security(sid)
        self.s['accounts'].reopen(self.aid);self.s['instruments'].unlink(self.hid);c.delete_security(sid)
        self.s['companies'].save(Company('Issuer',status='archived',id=cid))
        with self.assertRaises(RecordError):self.issue(cid)
        with self.assertRaises(RecordError):c.assign_sector(cid,self.sector('finance')['id'])

    def test_ledger_transfer_requires_the_same_issue_not_just_issuer_and_ticker(self):
        cid=self.issuer();first=self.issue(cid);second=self.issue(cid,'preferred_share')
        old=Position(**dict(self.vault.con.execute('SELECT * FROM holdings WHERE id=?',(self.hid,)).fetchone()))
        self.s['positions'].save(replace(old,ticker='SAME'));self.s['instruments'].link(self.hid,'security',first);self.s['ledger'].activate(self.aid)
        aid=self.s['save_financial_account'].execute(Account('Second'))
        hid=self.s['positions'].save(Position(aid,name='Target',ticker='SAME',quantity=0,price=50));self.s['ledger'].activate(aid)
        entry=Entry(self.day,'transfer',self.hid,1,50,target_id=hid,request_id='issue-transfer')
        for issue in (None,second):
            self.s['instruments'].link(hid,'company',cid,issue);before=self.dump()
            with self.assertRaises(RecordError):self.s['ledger'].post(entry)
            self.assertEqual(before,self.dump());self.s['instruments'].unlink(hid)
        self.s['instruments'].link(hid,'security',first);self.s['ledger'].post(entry)
        self.assertEqual(self.s['instrument_reports'].detail('company',cid)['totals']['RUB']['known'],100)
        self.assertEqual(len(self.s['instrument_reports'].detail('security',first)['positions']),2)

    def test_disk_failure_rolls_back_multi_record_changes_and_ciphertext(self):
        c=self.command();cid=self.issuer();sid=self.issue(cid);lid=c.save_listing(Listing(sid,'MOEX','OLD',valid_from=self.days(20)))
        sector=self.sector('finance');before=self.dump();disk=Path(self.vault.path).read_bytes()
        actions=[lambda:c.assign_sector(cid,sector['id'],True),lambda:c.save_sector(replace(Sector(**sector),name='Changed')),
                 lambda:c.save_security(Security(cid,'New','bond')),lambda:c.rename_listing(lid,'NEW',self.days(2)),
                 lambda:c.save_listing(Listing(sid,'OTHER','NEW',status='active',as_of=self.day,source='Notice')),
                 lambda:self.s['instruments'].link(self.hid,'security',sid),lambda:c.delete_listing(lid),lambda:c.delete_security(sid)]
        for action in actions:
            with patch('vault.os.replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):action()
            self.assertEqual(before,self.dump());self.assertEqual(disk,Path(self.vault.path).read_bytes())

    def test_foreign_keys_prevent_cross_issuer_position_links(self):
        cid=self.issuer();other=self.issuer();sid=self.issue(cid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.vault.con.execute('INSERT INTO holding_links(holding_id,company_id,security_id) VALUES (?,?,?)',(self.hid,other,sid))
        self.vault.con.rollback();self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_http_cards_forms_localization_escape_and_read_only(self):
        cid=self.issuer('<script>Issuer</script>');sid=self.issue(cid);lid=self.command().save_listing(Listing(sid,'MOEX','TEST',source='<script>source</script>'))
        self.s['instruments'].link(self.hid,'security',sid)
        self.s['planning'].allocate(self.pid,'holding',self.hid,100)
        for lang,label in [('ru','Сведения о выпуске'),('en','Issue details')]:
            self.s['change_language'].execute(lang);before=self.dump();disk=Path(self.vault.path).read_bytes()
            for path in ('/companies','/companies/sectors',f'/companies/{cid}',f'/securities/{sid}',f'/accounts/{self.aid}',f'/portfolios/{self.pid}'):
                response=self.client.get(path);self.assertEqual(response.status_code,200,path);body=response.get_data(as_text=True)
                self.assertNotIn('<script>Issuer</script>',body);self.assertNotIn('<script>source</script>',body)
                if path==f'/securities/{sid}':self.assertIn(label,body)
                if path==f'/accounts/{self.aid}':self.assertIn(f'/securities/{sid}',body)
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        response=self.client.post('/companies/sectors/save',data=dict(name='Sub',name_en='Sub',code='sub',parent_id=self.sector('finance')['id']))
        self.assertEqual(response.status_code,302);sub=self.sector('sub')['id']
        self.assertEqual(self.client.post(f'/companies/{cid}/sectors',data=dict(sector_id=sub,is_primary='1',as_of=self.day)).status_code,302)
        self.assertEqual(self.client.post(f'/companies/{cid}/sectors/{sub}/delete').status_code,302)
        self.assertEqual(self.client.post(f'/companies/sectors/{sub}/delete').status_code,302)
        response=self.client.post(f'/companies/{cid}/securities',data=dict(name='Bond',kind='bond',currency='rub'))
        self.assertEqual(response.status_code,302);bond=int(response.location.rsplit('/',1)[-1])
        self.assertEqual(self.client.post(f'/securities/{bond}/listings',data=dict(exchange='moex',ticker='BOND',status='unknown')).status_code,302)
        bl=next(l for l in self.data()['listings'] if l['security_id']==bond)
        self.assertEqual(self.client.post(f'/securities/listings/{bl["id"]}/rename',data=dict(ticker='NEWBOND',effective_date=self.day)).status_code,302)
        self.assertEqual(self.client.post(f'/securities/listings/{bl["id"]}/delete').status_code,302)
        self.assertEqual(self.client.post(f'/securities/{bond}/delete').status_code,302)
        self.assertEqual(self.client.get('/securities/999').status_code,404)
        self.assertEqual(self.client.post('/companies/sectors/save',data={}).status_code,400)
        self.assertEqual(self.client.get(f'/securities/{sid}/delete').status_code,405)
        self.client.post('/lock');self.assertEqual(self.client.get('/companies/sectors').status_code,302)
        with self.assertRaises(RuntimeError):Reader(self.vault).read()

    def test_validation_unknown_ids_and_malformed_identity(self):
        cid=self.issuer();sid=self.issue(cid);c=self.command()
        bad=[Sector('UPPER','Name'),Sector('ok',''),Sector('ok','Name',parent_id=0),Sector('ok','Name',source_url='javascript:evil'),
             Security(0,'X'),Security(cid,'X',kind='bad'),Security(cid,'X',isin='BAD'),Security(cid,'X',currency='rub'),Security(cid,'X',underlying_id=sid),
             Security(cid,'X','depositary_receipt',underlying_id=-1),Listing(sid,'','X'),Listing(sid,'MOEX','X',valid_from='bad'),
             Listing(sid,'MOEX','X',valid_from='2020-01-02',valid_to='2020-01-01'),Listing(sid,'MOEX','X',status='bad'),Listing(sid,'MOEX','X',status='active')]
        for r in bad:
            with self.assertRaises(RecordError):r.validate()
        with self.assertRaises(RecordError):c.assign_sector(cid,self.sector('finance')['id'],as_of='9999-12-31')
        with self.assertRaises(RecordError):c.save_security(Security(cid,'DR','depositary_receipt',underlying_id=self.issue(cid,'bond')))
        for action in (lambda:c.delete_sector(999),lambda:c.assign_sector(999,1),lambda:c.remove_sector(cid,999),lambda:c.delete_security(999),lambda:c.delete_listing(999),lambda:c.save_listing(Listing(999,'MOEX','X'))):
            with self.assertRaises(EntityNotFound):action()
        self.assertIsNone(security_class('unknown'))

    def test_all_catalog_messages_have_english_translations(self):
        en=json.loads(Path('wealth_manager/adapters/web/locales/en.json').read_text());messages=set()
        for path in list(Path('wealth_manager/modules/market_catalog').glob('*.py'))+[Path('wealth_manager/adapters/persistence/market_catalog.py')]:
            for n in ast.walk(ast.parse(path.read_text())):
                if isinstance(n,ast.Constant) and isinstance(n.value,str) and re.search('[А-Яа-яЁё]',n.value):messages.add(n.value)
        for name in ('sectors.html','sector_form.html','security_form.html','listing_form.html','_company_catalog.html','_security_catalog.html'):
            content=(Path('templates')/name).read_text();messages.update(re.findall(r"_\('([^']+)'\)",content));messages.update(re.findall(r"fields\.(?:input|select|area)\('[^']+','([^']+)'",content))
        self.assertEqual(sorted(messages-set(en)),[])


class CatalogMigrationTests(unittest.TestCase):
    def test_v18_upgrade_preserves_ids_positions_allocations_and_unknowns_and_is_atomic(self):
        f=fixtures.ValuationIntegrationTests();self.addCleanup(f.doCleanups)
        with patch('db._migrate_market_catalog'),patch('db._migrate_sector_scoring'),patch('db._migrate_listing_iss'):f.setUp()
        con=f.vault.con
        con.execute("INSERT INTO companies(id,name,ticker,exchange,country,sector,status) VALUES (42,'Legacy','OLD','MOEX','RU','Unclear legacy sector','watch')")
        con.execute('INSERT INTO holding_links(holding_id,company_id) VALUES (?,42)',(f.hid,));con.commit()
        f.s['planning'].allocate(f.pid,'holding',f.hid,100);f.vault.save(force=True)
        before=list(con.iterdump());disk=Path(f.vault.path).read_bytes();original=db._migrate_market_catalog
        def failed(c):original(c);raise RuntimeError('migration failed')
        with patch('db._migrate_market_catalog',side_effect=failed):
            with self.assertRaises(RuntimeError):db.init_db(con)
        self.assertEqual(before,list(con.iterdump()))
        with patch('vault.os.replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):initialize_vault(f.vault)
        self.assertEqual(before,list(con.iterdump()));self.assertEqual(disk,Path(f.vault.path).read_bytes())
        initialize_vault(f.vault);data=Reader(f.vault).read()
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],db.SCHEMA_VERSION)
        self.assertEqual(data['companies'][0]['id'],42);self.assertEqual(data['companies'][0]['sector'],'Unclear legacy sector')
        self.assertEqual(data['securities'][0]['kind'],'unknown');self.assertEqual(data['listings'][0]['status'],'unknown');self.assertEqual(data['listings'][0]['as_of'],'')
        self.assertEqual(data['memberships'],[]);self.assertIsNone(data['links'][0]['security_id']);self.assertEqual(data['links'][0]['company_id'],42)
        self.assertEqual(f.s['planning_reports'].goal_detail(f.gid)['goal']['total'],100)
        self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
        after=list(con.iterdump());db.init_db(con);self.assertEqual(after,list(con.iterdump()))
        f.vault.lock();f.vault.unlock('password123');self.assertEqual(Reader(f.vault).read(),data)
