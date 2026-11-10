"""Detached read model for the local, single-user dataset."""
class SqliteReportRepository:
    def __init__(self, vault): self.vault = vault

    def read(self):
        with self.vault.lock_:
            if not self.vault.unlocked: raise RuntimeError('Vault is locked')
            return read_connection(self.vault.con)


def read_connection(connection):
    return {table: [dict(row) for row in connection.execute('SELECT * FROM ' + table)]
            for table in ('accounts','holdings','cashflows','leases','rent_payments','rates','snapshots','settings')}
