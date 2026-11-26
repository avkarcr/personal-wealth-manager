"""M11 acceptance with synthetic issuer records and ISS responses. No live requests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import db
import test_market_catalog as catalog_fixtures
from wealth_manager.adapters.market_data.iss import IssProvider
from wealth_manager.adapters.persistence.market_catalog import Repository
from wealth_manager.adapters.persistence.migrations import initialize_vault
from wealth_manager.modules.companies.domain import Company
from wealth_manager.modules.macroeconomics.refresh import RefreshError
from wealth_manager.modules.market_catalog.domain import Listing, Sector, Security, find
from wealth_manager.modules.positions.domain import Position
from wealth_manager.shared.records import RecordError


ISIN = 'RU0000000001'
REFERENCE_FIELDS = ['SECID', 'BOARDID', 'ISIN', 'REGNUMBER', 'SECNAME', 'PREVDATE', 'PREVPRICE']
CANDLE_FIELDS = ['begin', 'end', 'close', 'value']


def table(block, columns, rows):
    return json.dumps({block: {'columns': columns, 'data': rows}}).encode()


def reference(ticker, *, isin=ISIN, registration_number='SYNTH-01', day=None, price=None, board='TQBR'):
    return table('securities', REFERENCE_FIELDS, [
        [ticker, board, isin, registration_number, 'Synthetic ISS name', day, price],
    ])


def candle(day, close, turnover):
    return [day + ' 00:00:00', day + ' 23:59:59', close, turnover]


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        fixture = catalog_fixtures.CatalogTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        self.vault = fixture.vault
        self.client = fixture.client
        self.s = fixture.app.extensions['wealth_manager'].services
        self.sync = self.s['listing_iss']
        self.sync.clock = lambda: datetime.combine(fixture.today, time(12), tzinfo=timezone.utc)
        self.transport = Mock()
        self.sync.provider = IssProvider(self.transport)
        self.finance = fixture.sector('finance')['id']
        self.tech = fixture.sector('technology')['id']

    def listing(self, ticker='SYNTH', *, name='Synthetic company', isin=ISIN, start=10, **fields):
        cid = self.s['companies'].save(Company(name, thesis='Synthetic notes'))
        sid = self.fixture.command().save_security(Security(cid, name + ' shares', 'common_share', isin=isin))
        lid = self.fixture.command().save_listing(Listing(
            sid, 'MOEX', ticker, fields.pop('board', 'TQBR'),
            valid_from=self.fixture.days(start) if start is not None else '', **fields,
        ))
        return cid, sid, lid

    def enable(self, mode='familiarization'):
        self.s['macro_scheduler'].configure('key_rate', False, self.fixture.days(2), 24, mode, self.vault.token)

    def refresh(self, lid, follow=False):
        return self.client.post(f'/securities/listings/{lid}/iss', follow_redirects=follow)

    def saved(self, lid):
        return Repository(self.vault.con).iss_target(lid).saved

    def result_ids(self, **kwargs):
        return [row['id'] for row in self.s['company_reports'].overview(**kwargs)['companies']]

    def test_combined_filters_historical_ticker_and_existing_cards(self):
        cid, sid, lid = self.listing('SYNTH_OLD', name='Альфа <эмитент>')
        fid = self.fixture.command().save_sector(Sector('synth_industry', 'Банки', 'Banks', self.finance))
        self.fixture.command().assign_sector(cid, self.finance)
        self.fixture.command().assign_sector(cid, fid, True)
        self.fixture.command().rename_listing(lid, 'SYNTH_NEW', self.fixture.days(4))
        preference = self.fixture.issue(cid, 'preferred_share', registration_number='SYNTH-PREF')
        self.fixture.command().save_listing(Listing(preference, 'MOEX', 'SYNTHP', 'TQBR'))
        other, _, _ = self.listing('SYNTH_OTHER', name='Янтарь', isin='RU0000000002')
        self.fixture.command().assign_sector(other, self.finance)
        outside = self.s['companies'].save(Company('Synthetic private', status='archived'))

        self.assertEqual(self.result_ids(sector=str(self.finance)), [cid, other])
        self.assertEqual(self.result_ids(sector=str(fid), query='synth_old', scope='moex', status='watch'), [cid])
        for term in ('АЛЬФА', 'synth_new', 'synth-pref', ISIN.lower()):
            self.assertEqual(self.result_ids(query=term), [cid])
        self.assertEqual(self.result_ids(sector=str(self.tech)), [])
        self.assertEqual(self.result_ids(sector='unclassified', status='archived'), [outside])
        self.assertEqual(self.result_ids(scope='moex', order='name_desc'), [other, cid])
        self.assertEqual(self.result_ids(sector='9000000', query='synth'), [])

        self.s['change_language'].execute('en')
        before = self.fixture.dump()
        response = self.client.get('/companies', query_string={'sector': fid, 'q': 'synTH_olD'})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Financials / Banks', html)
        self.assertIn('Ended listing', html)
        self.assertIn(f'/companies/{cid}', html)
        self.assertIn(f'/securities/{sid}', html)
        self.assertIn('Альфа &lt;эмитент&gt;', html)
        self.assertNotIn(f'id="company-{other}"', html)
        self.assertIn('Industry unavailable', self.client.get('/companies?sector=9000000').get_data(as_text=True))
        self.assertIn('No companies match these filters', self.client.get('/companies?q=absent').get_data(as_text=True))
        industry = self.client.get(f'/macroeconomics/sectors/{fid}').get_data(as_text=True)
        self.assertIn(f'href="/companies?sector={fid}"', industry)
        self.assertEqual(before, self.fixture.dump())
        self.transport.get.assert_not_called()

    def test_watchlist_requires_post_and_uses_only_explicit_portfolio_links(self):
        cid = self.s['companies'].save(Company('Synthetic watched', status='owned', thesis='User notes'))
        other = self.s['companies'].save(Company('Synthetic unlinked', ticker='TEST', exchange='MOEX', status='owned'))
        position = self.s['positions'].save(Position(self.fixture.aid, ticker='TEST', quantity=15, price=10))
        self.s['instruments'].link(self.fixture.hid, 'company', cid)
        self.s['instruments'].link(position, 'company', cid)
        for hid in (self.fixture.hid, position):
            self.s['planning'].allocate(self.fixture.pid, 'holding', hid, 40)
        before_wealth = self.s['reports'].dashboard()
        before_links = self.fixture.data()['links']
        overview = self.s['company_reports'].overview()['companies']
        self.assertEqual([p['id'] for p in find(overview, cid)['portfolios']], [self.fixture.pid])
        self.assertEqual(find(overview, other)['portfolios'], [])
        self.assertEqual(self.client.get(f'/companies/{cid}/watch').status_code, 405)
        response = self.client.post(f'/companies/{cid}/watch', follow_redirects=True)
        self.assertIn('Наблюдаю', response.get_data(as_text=True))
        self.assertEqual(self.result_ids(status='watch'), [cid])
        self.assertEqual(find(self.fixture.data()['companies'], cid)['thesis'], 'User notes')
        self.assertEqual(self.fixture.data()['links'], before_links)
        self.assertEqual(self.s['reports'].dashboard(), before_wealth)
        encrypted = Path(self.vault.path).read_bytes()
        self.assertEqual(self.client.post(f'/companies/{cid}/watch').status_code, 302)
        self.assertEqual(Path(self.vault.path).read_bytes(), encrypted)
        self.assertEqual(self.client.post('/companies/999999/watch').status_code, 404)
        for hid in (self.fixture.hid, position):
            original = next(r for r in self.s['instrument_reports'].detail('company', cid)['positions'] if r['id'] == hid)
            self.s['positions'].save(Position(self.fixture.aid, name=original['name'], ticker=original['ticker'],
                                               id=hid, quantity=0, price=10))
        report = find(self.s['company_reports'].overview()['companies'], cid)
        self.assertEqual(report['portfolios'], [])
        self.assertIn(f'/portfolios/{self.fixture.pid}', self.client.get(f'/companies/{cid}').get_data(as_text=True))

    def test_payload_identity_daily_metrics_and_idempotent_encrypted_storage(self):
        cid, sid, lid = self.listing()
        self.s['instruments'].link(self.fixture.hid, 'security', sid)
        self.assertIn('Выберите режим доступа MOEX', self.client.get(f'/securities/{sid}').get_data(as_text=True))
        self.refresh(lid)
        self.transport.get.assert_not_called()
        self.enable()
        latest, prior = self.fixture.days(1), self.fixture.days(3)
        self.transport.get.side_effect = [
            reference('SYNTH', day=latest, price=104.75),
            table('candles', CANDLE_FIELDS, [candle(latest, 105, 0), candle(prior, 100, None)]),
        ] * 2
        before = self.fixture.data()
        balances = self.s['reports'].dashboard()
        response = self.refresh(lid, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Сведения ISS обновлены', response.get_data(as_text=True))
        self.assertIn('Дневной оборот', response.get_data(as_text=True))
        self.assertIn('SYNTH-01', response.get_data(as_text=True))
        calls = self.transport.get.call_args_list
        first, second = (urlsplit(call.args[0]) for call in calls)
        self.assertEqual((first.scheme, first.hostname), ('https', 'iss.moex.com'))
        self.assertTrue(first.path.endswith('/TQBR/securities/SYNTH.json'))
        self.assertTrue(second.path.endswith('/TQBR/securities/SYNTH/candles.json'))
        self.assertEqual(parse_qs(second.query)['from'], [self.fixture.days(10)])
        self.assertEqual(parse_qs(second.query)['till'], [latest])
        self.assertEqual(parse_qs(second.query)['interval'], ['24'])
        snapshot = self.saved(lid)
        self.assertEqual(snapshot.quote.day.isoformat(), latest)
        self.assertEqual(snapshot.quote.previous_day.isoformat(), prior)
        self.assertEqual((snapshot.quote.close, snapshot.quote.change, snapshot.quote.turnover),
                         (Decimal('105'), Decimal('0.05'), Decimal(0)))
        self.assertEqual(self.result_ids(query='synth-01'), [cid])
        self.assertEqual(before, self.fixture.data())
        self.assertEqual(balances, self.s['reports'].dashboard())
        first_save = Path(self.vault.path).read_bytes()
        self.refresh(lid)
        self.assertEqual(Path(self.vault.path).read_bytes(), first_save)
        self.vault.lock()
        self.vault.unlock('password123')
        self.assertEqual(self.saved(lid), snapshot)
        self.assertEqual(self.vault.con.execute('PRAGMA foreign_key_check').fetchall(), [])
        listing = Listing(**find(self.fixture.data()['listings'], lid))
        self.fixture.command().save_listing(replace(listing, valid_from=latest))
        issues = find(self.s['company_reports'].overview()['companies'], cid)['catalog']['securities']
        narrowed = find(issues, sid)['listings'][0]['iss'].quote
        self.assertEqual(narrowed.close, snapshot.quote.close)
        self.assertIsNone(narrowed.change)

    def test_missing_quote_previous_trade_and_older_data(self):
        _, _, lid = self.listing(start=None)
        self.enable('agreement')
        self.transport.get.return_value = reference('SYNTH')
        saved = self.sync.execute(lid, self.vault.token)
        self.assertIsNone(saved.quote)
        self.assertEqual(saved.access, 'agreement')
        self.assertEqual(self.transport.get.call_count, 1)

        self.transport.get.return_value = reference('SYNTH', day=self.fixture.days(2), price=101.25)
        saved = self.sync.execute(lid, self.vault.token)
        self.assertEqual(saved.quote.basis, 'previous')
        self.assertEqual(saved.quote.close, Decimal('101.25'))
        self.assertIsNone(saved.quote.change)
        self.assertIsNone(saved.quote.turnover)
        self.assertEqual(self.transport.get.call_count, 2)

        self.transport.get.return_value = reference('SYNTH', day=self.fixture.days(4), price=99)
        self.assertEqual(self.sync.execute(lid, self.vault.token).quote, saved.quote)
        self.transport.get.side_effect = RefreshError('Источник рынка недоступен')
        self.assertIn('Источник рынка недоступен', self.refresh(lid, follow=True).get_data(as_text=True))
        self.assertEqual(self.saved(lid), saved)

    def test_turnover_orders_only_comparable_dates_and_preserves_true_zero(self):
        no_data, _, _ = self.listing('INACTIVE', name='A missing', isin='RU0000000002')
        trade, _, lid_trade = self.listing('TRADED', name='B trade')
        zero, _, lid_zero = self.listing('ZERO', name='C zero', isin='RU0000000003')
        earlier, _, lid_earlier = self.listing('EARLIER', name='D earlier', isin='RU0000000004')
        latest, old = self.fixture.days(1), self.fixture.days(2)
        self.enable()
        self.transport.get.side_effect = [
            reference('TRADED'), table('candles', CANDLE_FIELDS, [candle(latest, 123, 500)]),
            reference('ZERO', isin='RU0000000003'), table('candles', CANDLE_FIELDS, [candle(latest, 1, 0)]),
            reference('EARLIER', isin='RU0000000004'), table('candles', CANDLE_FIELDS, [candle(old, 99, 9000)]),
        ]
        for lid in (lid_trade, lid_zero, lid_earlier):
            self.sync.execute(lid, self.vault.token)
        self.assertEqual(self.result_ids(order='turnover'), [trade, zero, no_data, earlier])
        suspended = Listing(**find(self.fixture.data()['listings'], lid_trade))
        self.fixture.command().save_listing(replace(suspended, status='suspended', as_of=self.fixture.day, source='Synthetic'))
        self.assertEqual(self.result_ids(order='turnover'), [zero, no_data, trade, earlier])
        self.assertIn(latest, self.client.get('/companies?order=turnover').get_data(as_text=True))
        self.assertIsNone(self.s['company_reports'].overview(query='A missing', order='turnover')['turnover_day'])

    def test_does_not_write_on_conflicts_provider_errors_or_disk_failure(self):
        _, sid, lid = self.listing(start=None)
        self.enable()
        self.transport.get.return_value = reference('SYNTH', day=self.fixture.days(2), price=100)
        self.sync.execute(lid, self.vault.token)
        self.transport.get.return_value = reference('SYNTH', isin='RU0000000002')
        self.s['change_language'].execute('en')
        before, disk = self.fixture.dump(), Path(self.vault.path).read_bytes()
        self.assertIn('Issue identifiers differ', self.refresh(lid, follow=True).get_data(as_text=True))
        self.assertEqual(before, self.fixture.dump())
        self.assertEqual(disk, Path(self.vault.path).read_bytes())
        self.s['change_language'].execute('ru')
        before = self.fixture.dump()
        disk = Path(self.vault.path).read_bytes()
        self.transport.get.return_value = reference('SYNTH', day=self.fixture.days(1), price=105)
        with patch('vault.os.replace', side_effect=OSError('Synthetic disk failure')):
            self.assertIn('Не удалось сохранить сведения ISS', self.refresh(lid, follow=True).get_data(as_text=True))
        self.assertEqual(before, self.fixture.dump())
        self.assertEqual(disk, Path(self.vault.path).read_bytes())
        self.assertFalse(self.vault.dirty())

        changed = Security(**find(self.fixture.data()['securities'], sid))
        self.fixture.command().save_security(replace(changed, isin='RU0000000002'))
        html = self.client.get(f'/securities/{sid}').get_data(as_text=True)
        self.assertNotIn('Последняя сделка предыдущей сессии', html)
        self.assertIn('Цена ISS пока недоступна', html)

    def test_request_drops_response_after_catalog_or_vault_changes(self):
        _, _, lid = self.listing(start=None)
        self.enable()

        def update_during_io(url):
            listing = Listing(**find(self.fixture.data()['listings'], lid))
            with ThreadPoolExecutor(1) as pool:
                update = pool.submit(self.fixture.command().save_listing,
                                     replace(listing, status='suspended', as_of=self.fixture.day, source='Synthetic'))
                update.result(timeout=5)
            return reference('SYNTH')
        self.transport.get.side_effect = update_during_io
        self.assertIn('Листинг или режим доступа изменился', self.refresh(lid, follow=True).get_data(as_text=True))
        self.assertIsNone(self.saved(lid))

        listing = Listing(**find(self.fixture.data()['listings'], lid))
        self.fixture.command().save_listing(replace(listing, status='active'))
        def lock_during_io(url):
            self.vault.lock()
            self.vault.unlock('password123')
            return reference('SYNTH')
        self.transport.get.side_effect = lock_during_io
        response = self.refresh(lid)
        self.assertIn('/unlock', response.location)
        self.assertIsNone(self.saved(lid))

    def test_ineligible_listing_and_no_lookup_by_ticker_alone(self):
        self.enable()
        _, sid, lid = self.listing(isin='')
        self.assertIn('Для ISS укажите ISIN или регистрационный номер выпуска',
                      self.client.get(f'/securities/{sid}').get_data(as_text=True))
        self.assertIn('Для ISS укажите ISIN', self.refresh(lid, follow=True).get_data(as_text=True))
        _, _, separate = self.listing('OTHERBOARD', isin='RU0000000002', board='SMAL')
        self.assertIn('Листинг недоступен', self.refresh(separate, follow=True).get_data(as_text=True))
        self.assertEqual(self.refresh(999999).status_code, 404)
        self.transport.get.assert_not_called()
        security = Security(**find(self.fixture.data()['securities'], sid))
        self.fixture.command().save_security(replace(security, registration_number='SYNTH-01'))
        self.transport.get.side_effect = [
            reference('SYNTH'), table('candles', CANDLE_FIELDS, []),
        ]
        self.refresh(lid)
        self.assertEqual(self.saved(lid).reference.isin, ISIN)
        self.assertEqual(find(self.fixture.data()['securities'], sid)['isin'], '')


class IssContractTests(unittest.TestCase):
    def test_rejects_invalid_reference_shapes_codes_dates_and_amounts(self):
        http = Mock()
        provider = IssProvider(http)
        with self.assertRaises(RecordError):
            provider.reference('../x')
        http.get.assert_not_called()
        cases = [
            b'not JSON', b'{}', table('securities', REFERENCE_FIELDS[:-1], []),
            table('securities', REFERENCE_FIELDS, [['SYNTH']]),
            reference('OTHER'), reference('SYNTH', board='SMAL'), reference('SYNTH', isin='BAD'),
            reference('SYNTH', day='2025-03-99', price=10),
            reference('SYNTH', day='2025-03-06', price=True),
            reference('SYNTH', day='2025-03-06', price=float('nan')),
            reference('SYNTH', day='2025-03-06', price=0),
        ]
        for body in cases:
            with self.subTest(body=body), self.assertRaisesRegex(RecordError, 'Некорректный ответ ISS'):
                http.get.return_value = body
                provider.reference('SYNTH')
        http.get.return_value = table('securities', REFERENCE_FIELDS, [])
        with self.assertRaisesRegex(RecordError, 'ISS не вернул'):
            provider.reference('SYNTH')

    def test_rejects_duplicate_candles_wrong_period_and_negative_turnover(self):
        http = Mock()
        provider = IssProvider(http)
        d = datetime(2025, 3, 6).date()
        day = d.isoformat()
        for rows in (
            [candle(day, 10, 1)] * 2, [candle('2025-03-07', 11, 50)], [candle(day, 10, -1)],
            [[day + 'T15:00:00', day + 'T16:00:00', 2, 3]],
        ):
            with self.subTest(rows=rows), self.assertRaises(RecordError):
                http.get.return_value = table('candles', CANDLE_FIELDS, rows)
                provider.candles('SYNTH', d - timedelta(days=5), d)
        http.get.return_value = table('candles', CANDLE_FIELDS, [candle(day, 10, None)])
        self.assertIsNone(provider.candles('SYNTH', d, d)[0].turnover)


class IssMigrationTests(unittest.TestCase):
    def test_populated_v20_rolls_back_and_preserves_existing_catalog_and_links(self):
        f = catalog_fixtures.CatalogTests()
        self.addCleanup(f.doCleanups)
        with patch('db._migrate_listing_iss'):
            f.setUp()
        cid = f.issuer()
        sid = f.issue(cid, isin=ISIN)
        lid = f.command().save_listing(Listing(sid, 'MOEX', 'SYNTH', 'TQBR'))
        f.s['instruments'].link(f.hid, 'security', sid)
        f.s['planning'].allocate(f.pid, 'holding', f.hid, 40)
        data, before = f.data(), f.dump()
        disk = Path(f.vault.path).read_bytes()
        self.assertEqual(f.vault.con.execute('PRAGMA user_version').fetchone()[0], 20)
        with patch('vault.os.replace', side_effect=OSError('Synthetic disk failure')), self.assertRaises(OSError):
            initialize_vault(f.vault)
        self.assertEqual(f.dump(), before)
        self.assertEqual(Path(f.vault.path).read_bytes(), disk)
        self.assertEqual(f.vault.con.execute('PRAGMA user_version').fetchone()[0], 20)
        initialize_vault(f.vault)
        f.vault.lock()
        f.vault.unlock('password123')
        self.assertEqual(f.data(), data)
        self.assertIsNone(Repository(f.vault.con).iss_target(lid).saved)
        self.assertEqual(f.vault.con.execute('PRAGMA user_version').fetchone()[0], 21)
        self.assertEqual(f.vault.con.execute('PRAGMA foreign_key_check').fetchall(), [])
