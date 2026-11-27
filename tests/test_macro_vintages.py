"""M12 replay of dated OECD releases from ALFRED (offline)."""
import calendar
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from vault_fixtures import ResearchCase
from test_macro_cbr import provider
from wealth_manager.modules.economic_cycle.config import defaults
from wealth_manager.modules.macroeconomics.data import Artifact, Indicator, Point, Series, Source

ARCHIVE = Path(__file__).parent / 'fixtures' / 'alfred'
VINTAGES = ('2020-09-30', '2021-09-30', '2022-09-30')
GDP = 'NAEXKP01RUQ652S'
CPI = 'CPALTT01RUM659N'


def observations(series, vintage):
    with (ARCHIVE / f'{series}-{vintage}.csv').open() as handle:
        reader = csv.DictReader(handle)
        return [(date.fromisoformat(row['observation_date']), Decimal(row[series + '_' + vintage.replace('-', '')]))
                for row in reader]


def replay(commands, vintage):
    nominal = observations('RUSGDPNADSMEI', VINTAGES[0])[0][1]
    source = Source('alfred_oecd', 'OECD via ALFRED')
    for series, code in ((GDP, 'gdp_real_level_sa'), (CPI, 'cpi_yoy_growth')):
        values = observations(series, vintage)
        quarterly = series == GDP
        # Express each volume vintage in comparable 2015 rubles using the
        # nominal 2015 total already observable in the earliest archive.
        scale = nominal / sum(v for d, v in values if d.year == 2015) if quarterly else Decimal(1)
        definition = Series(code, 'RU', series, 'RUB' if quarterly else 'percent',
                            'quarterly' if quarterly else 'monthly', adjustment='sa' if quarterly else 'nsa',
                            base_year=2015 if quarterly else None, methodology='M12-archive-replay-v1')
        points = []
        for start, value in values:
            month = start.month + (2 if quarterly else 0)
            end = date(start.year, month, calendar.monthrange(start.year, month)[1])
            points.append(Point(start.isoformat(), end.isoformat(), value * scale))
        path = ARCHIVE / f'{series}-{vintage}.csv'
        packet = Artifact(path.name, path.read_bytes(), 'alfred-csv-v1', 'text/csv')
        # Conservative availability bound: end of archive day. Original
        # intraday publication times are unknown.
        with patch('wealth_manager.adapters.persistence.macro_data.utcnow',
                   return_value=vintage + 'T23:59:59.999999+00:00'):
            commands.ingest(Indicator(code, code, code), source, definition, packet, points)


def bind_archives(cycle, token):
    state = cycle.settings(token)
    config = defaults()
    roles = {GDP: 'gdp', CPI: 'cpi', 'key_rate': 'key_rate',
             'ofz_zero_1y': 'yield_1y', 'ofz_zero_2y': 'yield_2y', 'ofz_zero_10y': 'yield_10y'}
    for series in state['choices']:
        role = roles.get(series['provider_key'])
        if role and (series['methodology'] == 'M12-archive-replay-v1' or series['methodology'].startswith('cbr_hd_base_')):
            config['series'][role] = series['series_id']
    return config, cycle.configure(token, config, state['config_id'])


class VintageTests(ResearchCase):

    def test_archive_replay_survives_revisions_and_pandemic_shock(self):
        dashboard = self.s['macro_dashboard']
        cycle = self.s['economic_cycle']
        token = self.vault.token
        replay(self.s['macro_data'], VINTAGES[0])
        config, config_id = bind_archives(cycle, token)

        def report(cutoff, mode='known'):
            return dashboard.overview(token, as_of=cutoff, mode=mode, chart='gap', window='1')

        before = report(VINTAGES[0])
        self.assertEqual(report('2020-09-29')['result']['inputs'], [])
        for vintage in VINTAGES[1:]:
            replay(self.s['macro_data'], vintage)
        self.assertEqual(before['fingerprint'], report(VINTAGES[0])['fingerprint'])
        revised = report(VINTAGES[0], 'latest')
        self.assertNotEqual(before['result']['inputs'], revised['result']['inputs'])
        self.assertEqual(before['graph']['points'][-1]['date'], '2020-03-31')
        self.assertEqual(revised['graph']['points'][-1]['date'], '2020-09-30')
        strict_points = {p['date']: p['value'] for p in before['graph']['points']}
        latest_points = {p['date']: p['value'] for p in revised['graph']['points']}
        self.assertNotEqual(strict_points['2020-03-31'], latest_points['2020-03-31'])
        shock = next(p for p in revised['graph']['points'] if p['date'] == '2020-06-30')
        self.assertLess(shock['value'], -3)
        self.assertEqual(before['result']['phase'], 'INSUFFICIENT_DATA')
        self.assertIsNone(before['result']['features']['key_level']['value'])

        config['method'] = 'log_linear'
        config_id = cycle.configure(token, config, config_id)
        alternate = report(VINTAGES[0], 'latest')
        linear = next(p for p in alternate['graph']['points'] if p['date'] == shock['date'])
        self.assertGreater(abs(shock['value'] - linear['value']), Decimal('0.25'))
        config['groups']['activity'] = '80'
        cycle.configure(token, config, config_id)
        weighted = report(VINTAGES[0], 'latest')['result']
        self.assertGreater(weighted['coverage'], alternate['result']['coverage'])
        self.assertEqual(weighted['phase'], 'INSUFFICIENT_DATA')
        stale = report(VINTAGES[-1])['result']['features']['gdp_yoy']
        self.assertIsNone(stale['value'])
        self.assertEqual(stale['reason'], 'stale')

    def test_cbr_retrieval_only_extends_recalculated_history(self):
        commands = self.s['macro_data']
        for vintage in VINTAGES:
            replay(commands, vintage)
        for kind, name in [('key_rate', 'KeyRate'), ('ofz_curve', 'zcyc_params')]:
            raw = (ARCHIVE.parent / 'cbr' / (name + '-2020.html')).read_bytes()
            packets = provider(raw=raw).fetch(kind, date(2020, 6, 1), date(2020, 9, 30))
            with patch('wealth_manager.adapters.persistence.macro_data.utcnow',
                       return_value='2026-11-27T00:00:00+00:00'):
                for packet in packets:
                    commands.ingest(*packet)
        cycle = self.s['economic_cycle']
        token = self.vault.token
        bind_archives(cycle, token)
        strict = cycle.preview(token, VINTAGES[0])['result']
        result = self.s['macro_dashboard'].overview(token, VINTAGES[0], 'latest')
        latest = result['result']
        self.assertIsNone(strict['features']['key_level']['value'])
        self.assertEqual(latest['features']['key_level']['value'], Decimal('4.25'))
        self.assertEqual(latest['features']['spread_2']['value'], Decimal('1.78'))
        self.assertEqual(latest['coverage'], 70)
        self.assertEqual((latest['phase'], latest['transition']), ('RECOVERY', 'provisional'))
        scoring = self.s['sector_scoring'].preview(token, VINTAGES[0], 'latest')
        self.assertEqual(result['sector_result'], scoring['result'])
