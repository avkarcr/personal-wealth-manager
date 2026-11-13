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

    if connection.execute('PRAGMA user_version').fetchone()[0] >= 8:
        data['asset_dates'] = dict(connection.execute('SELECT id,valued_at FROM assets'))
    if connection.execute('PRAGMA user_version').fetchone()[0] >= 9:
        data['account_status_events'] = [dict(r) for r in connection.execute('SELECT * FROM account_status_events')]
        data['historical_rates'] = [dict(r) for r in connection.execute('SELECT * FROM historical_rates')]
    return data
