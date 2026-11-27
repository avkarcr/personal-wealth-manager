"""Manual Linux benchmark using a temporary vault and synthetic observations."""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from resource import RUSAGE_SELF, getrusage
from time import perf_counter

from test_economic_cycle import CycleTests
from wealth_manager.factory import create_app
from wealth_manager.modules.macroeconomics.data import Artifact, Indicator, Point, Series, Source


def measured(label, run):
    start = perf_counter()
    result = run()
    print(f'{label}: {perf_counter() - start:.3f} s; process peak RSS '
          f'{getrusage(RUSAGE_SELF).ru_maxrss / 1024:.1f} MiB', flush=True)
    return result


def main():
    fixture = CycleTests()
    fixture.setUp()
    try:
        config, _ = fixture.seed()
        cycle = fixture.service()
        vault = fixture.vault
        days = [date(2016, 11, 22) + timedelta(days=i) for i in range(3652)]
        for role in ('equities', 'bonds', 'money', 'key_rate', 'yield_1y', 'yield_2y', 'yield_10y'):
            code = {'equities': 'moex_imoex', 'bonds': 'moex_rgbitr', 'money': 'moex_rusfar',
                    'key_rate': 'key_rate'}.get(role, role.replace('yield_', 'ofz_zero_'))
            unit = 'index' if role in ('equities', 'bonds') else 'percent'
            series = Series(code, 'RU', code, unit, 'daily', adjustment='nsa', methodology='M12-synthetic')
            points = [Point(str(d), str(d), Decimal(100 + i % 13), str(d), 'date')
                      for i, d in enumerate(days) if d.weekday() < 5]
            packet = Artifact(code, ('Synthetic benchmark: ' + code).encode(), 'm12-bench')
            fixture.s['macro_data'].ingest(Indicator(code, code, code), Source('bench', 'Synthetic benchmark'),
                                           series, packet, points)
            config['series'][role] = vault.con.execute(
                'SELECT id FROM macro_series WHERE indicator=? AND methodology=?', (code, series.methodology)
            ).fetchone()[0]
        cycle.configure(vault.token, config, 1)
        path = Path(vault.path)
        print(f'{vault.con.execute("SELECT COUNT(*) FROM macro_revisions").fetchone()[0]} revisions; '
              f'{path.stat().st_size} bytes before snapshots')
        app = create_app({'TESTING': True}, vault=vault)
        services = app.extensions['wealth_manager'].services
        services['macro_dashboard'].clock = cycle.clock
        services['sector_scoring'].clock = cycle.clock
        client = app.test_client()
        with client.session_transaction() as session:
            session['vault'] = vault.token
        for chart in ('equities', 'phases'):
            reads = []
            def trace(sql):
                if 'FROM macro_revisions r JOIN macro_series' in sql:
                    reads.append(1)
            vault.con.set_trace_callback(trace)
            try:
                response = measured(chart, lambda: client.get('/macroeconomics/dashboard', query_string={
                    'as_of': '2026-11-22', 'chart': chart, 'window': '10'}))
                assert response.status_code == 200
                print(f'{len(response.data)} response bytes; {len(reads)} full revision reads')
            finally:
                vault.con.set_trace_callback(None)
        for quarter in ('2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30'):
            measured('snapshot ' + quarter, lambda: cycle.save(vault.token, as_of=quarter))
        print(f'{path.stat().st_size} bytes after four saved snapshots')
        measured('encrypted save', lambda: vault.save(force=True))
        vault.lock()
        measured('unlock', lambda: vault.unlock('password123'))
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
