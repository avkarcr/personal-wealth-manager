"""Detached read model for the local, single-user dataset."""
class SqliteReportRepository:
    def __init__(self, vault): self.vault = vault

    def read(self):
        with self.vault.lock_:
            if not self.vault.unlocked: raise RuntimeError('Vault is locked')
            return read_connection(self.vault.con)


def read_connection(connection):
    data = {table: [dict(row) for row in connection.execute('SELECT * FROM ' + table)]
            for table in ('accounts','holdings','cashflows','leases','rent_payments','rates','snapshots','settings')}

    for f in data['cashflows']: f.update(ledger_owned=False,ledger_readonly=False)
    if connection.execute('PRAGMA user_version').fetchone()[0] >= 8:
        data['asset_dates'] = dict(connection.execute('SELECT id,valued_at FROM assets'))
    if connection.execute('PRAGMA user_version').fetchone()[0] >= 9:
        data['account_status_events'] = [dict(r) for r in connection.execute('SELECT * FROM account_status_events')]
        data['historical_rates'] = [dict(r) for r in connection.execute('SELECT * FROM historical_rates')]
    if connection.execute('PRAGMA user_version').fetchone()[0] >= 11:
        modes={r['account_id']:r for r in connection.execute('SELECT * FROM ledger_accounts')}
        for a in data['accounts']:
            row=modes.get(a['id'])
            a['ledger_started_on']=row['started_on'] if row else ''
            a['ledger_baseline_rub']=row['baseline_rub'] if row else 0
        invalid={tuple(r) for r in connection.execute('SELECT account_id,date FROM ledger_invalid_snapshots')}
        for r in data['snapshots']:r['invalidated']=(r['account_id'],r['date']) in invalid
        linked={r[0] for r in connection.execute('SELECT flow_id FROM ledger_flows')}
        for f in data['cashflows']:
            f['ledger_owned']=f['id'] in linked
            f['ledger_readonly']=f['account_id'] in modes
    if connection.execute('PRAGMA user_version').fetchone()[0] >= 12:
        for table in ('liabilities','liability_payments'):
            data[table]=[dict(r) for r in connection.execute('SELECT * FROM '+table)]
    return data
