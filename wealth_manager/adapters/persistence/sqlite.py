"""Adapters for the existing schema; REAL conversion is a migration boundary."""
from wealth_manager.modules.accounts.domain import AccountNotFound
from wealth_manager.modules.preferences.application import SUPPORTED_LANGUAGES

from .entities import SqliteFinancialAccountRepository as SqliteAccountRepository

class SqlitePreferencesRepository:
    def __init__(self, connection):
        self.connection = connection

    def get_language(self):
        row = self.connection.execute("SELECT value FROM settings WHERE key='language'").fetchone()
        return row[0] if row and row[0] in SUPPORTED_LANGUAGES else "ru"

    def set_language(self, language):
        self.connection.execute("INSERT INTO settings(key,value) VALUES ('language',?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (language,))
