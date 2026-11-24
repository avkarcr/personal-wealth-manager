"""M2: revision history, conservative publication dates and migration durability."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_research as fixtures
from wealth_manager.adapters.persistence.macro_data import Reader, Repository
from wealth_manager.modules.macroeconomics.data import Artifact, DataReports, Indicator, Point, Series, Source, instant
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.factory import create_app


class MacroDataTests(unittest.TestCase):
    setUp=fixtures.ResearchTests.setUp
    dump=fixtures.ResearchTests.dump
    definition=Indicator('cpi_index','Индекс ИПЦ','CPI index')
    source=Source('stats','Statistics',url='https://example.test',terms='Fixture')
    series=Series('cpi_index','RU','cpi','index','monthly',base_year=2020,adjustment='nsa')
    point=Point('2026-01-01','2026-01-31',D('101.000000000000000001'),'2026-02-10','date','preliminary')
    artifact=Artifact('fixture.json',b'{"index":"101.000000000000000001"}','fixture-v1','application/json')
    def ingest(self,point=None,series=None,source=None,artifact=None,now='2026-04-01T12:00:00Z'):
        with patch('wealth_manager.adapters.persistence.macro_data.utcnow',return_value=now):
            return self.s['macro_data'].ingest(self.definition,source or self.source,series or self.series,artifact or self.artifact,[point or self.point])
    def overview(self,when,mode='known'):return self.s['macro_data_reports'].overview(when,mode)

    def test_publication_revision_asof_and_late_arrival(self):
        first=self.ingest()[0]
        revised=replace(self.point,value=D('102'),published_at='2026-03-10',status='revised')
        second=self.ingest(revised)[0]
        self.assertEqual(self.overview('2026-02-09')['rows'],[])
        self.assertEqual(self.overview('2026-02-10T12:00:00Z')['rows'],[])
        self.assertEqual(self.overview('2026-02-10')['rows'][0]['revision_id'],first)
        self.assertEqual(self.overview('2026-03-10')['rows'][0]['revision_id'],second)
        self.assertEqual(self.overview('2026-02-10','latest')['rows'][0]['value'],102)
        # A late historical packet is not the latest economic revision.
        self.ingest(replace(self.point,value=D('100'),published_at='2026-02-01'))
        self.assertEqual(self.overview('2026-04-01')['rows'][0]['value'],102)
        self.assertEqual(self.ingest(),[first])
        self.assertEqual(len(Reader(self.vault).read()),3)
        self.assertEqual(self.overview('2026-02-10')['rows'][0]['value'],self.point.value)

    def test_unknown_publication_never_backfilled_from_period(self):
        self.ingest(replace(self.point,published_at='',publication_precision='unknown'))
        self.assertEqual(self.overview('2026-03-31')['rows'],[])
        row=self.overview('2026-04-01')['rows'][0]
        self.assertEqual(row['available_at'],instant('2026-04-01T12:00:00Z'))
        self.assertEqual(row['published_at'],'')
        self.assertEqual(self.overview('2026-01-31','latest')['rows'][0]['value'],self.point.value)
        self.assertEqual(self.overview('2026-01-30','latest')['rows'],[])

    def test_idempotence_provenance_exact_decimals_and_series_identity(self):
        ids=self.ingest();before=self.dump();self.assertEqual(self.ingest(),ids);self.assertEqual(self.dump(),before)
        for change in (dict(adjustment='sa'),dict(base_year=2021),dict(methodology='v2'),dict(unit='percent'),dict(scale=D(100))):
            self.ingest(series=replace(self.series,**change))
        rows=Reader(self.vault).read();self.assertEqual(len({r['series_id'] for r in rows}),6)
        self.assertTrue(all(r['value']==self.point.value for r in rows))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM macro_artifacts').fetchone()[0],1)
        self.assertEqual(self.vault.con.execute('SELECT content FROM macro_artifacts').fetchone()[0],self.artifact.content)
        self.assertEqual(self.s['macro_reports'].overview()['observations'],[])
        before=self.dump()
        with self.assertRaises(RecordError):self.ingest(source=replace(self.source,terms='changed'))
        self.assertEqual(self.dump(),before)

    def test_manual_edit_delete_history_and_period_correction(self):
        with patch('wealth_manager.adapters.persistence.macro_data.utcnow',return_value='2026-02-01T00:00:00Z'):
            rid=self.s['macroeconomics'].save(self.observation)
        with patch('wealth_manager.adapters.persistence.macro_data.utcnow',return_value='2026-02-02T00:00:00Z'):
            self.s['macroeconomics'].save(replace(self.observation,id=rid,value=D(12),date='2026-03-01'))
        self.assertEqual(self.overview('2026-02-01')['rows'][0]['value'],D('10.5'))
        self.assertEqual(self.overview('2026-02-02')['rows'],[]) # no resurrection of old period
        with patch('wealth_manager.adapters.persistence.macro_data.utcnow',return_value='2026-03-02T00:00:00Z'):
            self.s['macroeconomics'].delete(rid)
        self.assertEqual(self.overview('2026-03-01')['rows'][0]['record_id'],rid)
        self.assertEqual(self.overview('2026-03-02')['rows'],[])
        self.assertEqual(len(self.overview('2026-03-02')['history']),3)
        self.assertEqual(self.s['macro_reports'].overview()['observations'],[])

    def test_source_data_cannot_be_overwritten_by_manual_form(self):
        self.ingest();rid=Reader(self.vault).read()[0]['record_id'];before=self.dump()
        with self.assertRaises(EntityNotFound):self.s['macroeconomics'].save(replace(self.observation,id=rid))
        self.assertEqual(self.dump(),before)
        self.s['macroeconomics'].save(self.observation)
        self.assertEqual(len(Reader(self.vault).read()),2)

    def test_atomic_failed_save_and_encrypted_reopen(self):
        self.ingest();before=self.dump();disk=Path(self.vault.path).read_bytes()
        with patch.object(self.vault,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.ingest(replace(self.point,value=D(999)))
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.dump(),before)
        self.assertEqual(Reader(self.vault).read()[0]['value'],self.point.value)
        self.vault.lock()
        with self.assertRaises(RuntimeError):Reader(self.vault).read()

    def test_immutable_evidence(self):
        self.ingest()
        for table in ('macro_revisions','macro_artifacts'):
            for sql in ('DELETE FROM '+table,'UPDATE '+table+' SET id=id'):
                with self.assertRaises(sqlite3.IntegrityError):self.vault.con.execute(sql)
                self.vault.con.rollback()

    def test_validation_rejects_ambiguous_periods_before_writing(self):
        before=self.dump()
        cases=[dict(point=replace(self.point,period_end='2026-01-30')),
               dict(point=replace(self.point,period_start='2026-02-01')),
               dict(point=replace(self.point,publication_precision='unknown')),
               dict(point=replace(self.point,published_at='2026-02-10T10:00:00',publication_precision='instant')),
               dict(series=replace(self.series,base_year=True)),dict(series=replace(self.series,scale=D(0))),
               dict(series=replace(self.series,adjustment='invented')),dict(source=replace(self.source,key='legacy_manual')),
               dict(artifact=replace(self.artifact,content=b'')),dict(artifact=replace(self.artifact,content=b'x'*2000001))]
        for kwargs in cases:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises((RecordError,ValueError)):self.ingest(**kwargs)
        with self.assertRaises(RecordError):self.s['macro_data'].ingest(self.definition,self.source,self.series,self.artifact,[self.point,self.point])
        self.assertEqual(self.dump(),before)

    def test_calendar_boundaries_and_timezones(self):
        for frequency,start,end in [('monthly','2024-02-01','2024-02-29'),('quarterly','2026-10-01','2026-12-31'),('annual','2026-01-01','2026-12-31'),('daily','2026-01-01','2026-01-01')]:
            series=replace(self.series,frequency=frequency)
            Point(start,end,D(0)).validate(series)
            with self.assertRaises(RecordError):Point(start,'2027-01-01',D(0)).validate(series)
        self.assertEqual(instant('2026-02-10T15:00:00+03:00'),instant('2026-02-10T12:00:00Z'))
        for value in ('wrong','2026-02-30',None):
            with self.assertRaises(RecordError):instant(value,True)

    def test_read_only_localized_history_http_and_escape(self):
        self.ingest(replace(self.point,note='<script>alert(1)</script>'))
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as session:session['vault']=self.vault.token
        for lang,label in [('ru','Журнал версий'),('en','Revision history')]:
            self.s['change_language'].execute(lang);before=self.dump()
            response=client.get('/macroeconomics/data?as_of=2026-04-01');page=response.get_data(as_text=True)
            self.assertEqual(response.status_code,200);self.assertIn(label,page)
            self.assertIn('&lt;script&gt;',page);self.assertNotIn('<script>alert(1)</script>',page)
            self.assertEqual(self.dump(),before)
        for query in ('as_of=bad','mode=bad'):
            self.assertEqual(client.get('/macroeconomics/data?'+query).status_code,400)
        self.assertEqual(client.post('/macroeconomics/data').status_code,405)

    def test_migration_preserves_manual_identity_and_unknown_dates(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        with patch('db._migrate_macro_versions'),patch('db._migrate_macro_calculations'),patch('db._migrate_economic_cycle'),patch('db._migrate_market_catalog'):db.init_db(c)
        c.execute("INSERT INTO macro_observations(id,country,indicator,date,value,source,note) VALUES (42,'RU','key_rate','2020-01-01','10.1234567890123456789','manual','old note')");c.commit()
        with patch('wealth_manager.modules.macroeconomics.data.utcnow',return_value='2026-04-01T00:00:00.000000+00:00'):db.init_db(c)
        row=dict(c.execute('SELECT * FROM macro_observations').fetchone())
        self.assertEqual((row['id'],row['value'],row['note']),(42,'10.1234567890123456789','old note'))
        revision=dict(c.execute('SELECT * FROM macro_revisions').fetchone())
        self.assertEqual(revision['published_at'],'');self.assertEqual(revision['available_at'],'2026-04-01T00:00:00.000000+00:00')
        before=list(c.iterdump());db.init_db(c);self.assertEqual(list(c.iterdump()),before)
        rid=Repository(c).save(self.observation);self.assertGreater(rid,42)

    def test_migration_failure_restores_v15(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        with patch('db._migrate_macro_versions'),patch('db._migrate_macro_calculations'),patch('db._migrate_economic_cycle'),patch('db._migrate_market_catalog'):db.init_db(c)
        before=list(c.iterdump());original=db._migrate_macro_versions
        def fail(con):original(con);raise RuntimeError('migration failed')
        with patch('db._migrate_macro_versions',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(c)
        self.assertEqual(list(c.iterdump()),before)
        self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],15)
