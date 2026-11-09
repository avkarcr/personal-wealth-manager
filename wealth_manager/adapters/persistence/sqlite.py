"""Adapters for the existing schema; REAL conversion is a migration boundary."""
from wealth_manager.modules.accounts.domain import AccountNotFound
from wealth_manager.modules.preferences.application import SUPPORTED_LANGUAGES

class SqliteAccountRepository:
    def __init__(self, connection):
        self.connection = connection

    def save(self, account):
        data = (account.name, account.kind, account.institution, account.currency,
                float(account.current_value), float(account.tax_rate), account.note)
        if account.id is not None:
            result = self.connection.execute(
                "UPDATE accounts SET name=?,kind=?,institution=?,currency=?,current_value=?,tax_rate=?,note=? WHERE id=?",
                data + (account.id,))
            if result.rowcount != 1:
                raise AccountNotFound(account.id)
            return account.id
        return self.connection.execute(
            "INSERT INTO accounts(name,kind,institution,currency,current_value,tax_rate,note) VALUES (?,?,?,?,?,?,?)",
            data).lastrowid

    def record_purchase(self, account_id, currency, purchase):
        self.connection.execute(
            "INSERT INTO cashflows(account_id,date,type,amount,currency,note) VALUES (?,?,?,?,?,?)",
            (account_id, purchase.date.isoformat(), "deposit", float(purchase.amount), currency, "Покупка объекта"))

class SqlitePreferencesRepository:
    def __init__(self, connection):
        self.connection = connection

    def get_language(self):
        row = self.connection.execute("SELECT value FROM settings WHERE key='language'").fetchone()
        return row[0] if row and row[0] in SUPPORTED_LANGUAGES else "ru"

    def set_language(self, language):
        self.connection.execute("INSERT INTO settings(key,value) VALUES ('language',?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (language,))
