"""Serialize mutations with vault locking and restore memory if persistence fails."""
import sqlite3
import calc
from .entities import SqliteEntityRepository, SqliteAssetRepository, SqliteIncomeSourceRepository
from .market_data import SqliteMarketPreferences, SqliteRateRepository
from .operations import SqlitePositionRepository, SqliteCashflowRepository, SqliteRentalRepository, SqliteValuationRepository
from .sqlite import SqliteAccountRepository, SqlitePreferencesRepository

class VaultUnitOfWork:
    def __init__(self, vault):
        self.vault = vault
        self.committed = False

    def __enter__(self):
        self.vault.lock_.acquire()
        try:
            if not self.vault.unlocked:
                raise RuntimeError("Vault is locked")
            self.connection = self.vault.con
            if self.connection.in_transaction:
                raise RuntimeError("Cannot nest a unit of work in an active transaction")
            self.before = sqlite3.connect(":memory:")
            self.connection.backup(self.before)
            self.market_preferences = SqliteMarketPreferences(self.connection)
            self.rates = SqliteRateRepository(self.connection)
            self.positions = SqlitePositionRepository(self.connection)
            self.cashflows = SqliteCashflowRepository(self.connection)
            self.rentals = SqliteRentalRepository(self.connection)
            self.valuations = SqliteValuationRepository(self.connection)
            self.entities = SqliteEntityRepository(self.connection)
            self.assets = SqliteAssetRepository(self.connection)
            self.income_sources = SqliteIncomeSourceRepository(self.connection)
            self.accounts = SqliteAccountRepository(self.connection)
            self.preferences = SqlitePreferencesRepository(self.connection)
            return self
        except BaseException:
            self.vault.lock_.release()
            raise

    def take_snapshot(self):
        # Transitional bridge; calc remains legacy until reporting is extracted.
        calc.take_snapshot(self.connection, calc.Rates(self.connection), commit=False)

    def commit(self):
        self.connection.commit()
        self.vault.save(force=True)
        self.committed = True

    def __exit__(self, exc_type, exc, traceback):
        try:
            if not self.committed:
                self.connection.rollback()
                self.before.backup(self.connection)
                # after_request must not retry a failed operation implicitly.
                self.vault._saved_changes = self.connection.total_changes
        finally:
            self.before.close()
            self.vault.lock_.release()
