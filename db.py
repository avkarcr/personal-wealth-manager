"""Схема базы и миграции. Сама база хранится в зашифрованном виде — см. vault.py."""
import os
import sqlite3

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_PATH = os.environ.get("INVEST_VAULT", os.path.join(APP_DIR, "invest.enc"))
LEGACY_DB_PATH = os.environ.get("INVEST_DB", os.path.join(APP_DIR, "invest.db"))   # незашифрованная база v1–v2

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- broker | crypto | realty | other | freelance
    institution   TEXT DEFAULT '',
    currency      TEXT NOT NULL DEFAULT 'RUB',
    current_value REAL DEFAULT 0,          -- для недвижимости: текущая оценка
    tax_rate      REAL DEFAULT 0,          -- для фриланса: ставка налога, %
    note          TEXT DEFAULT '',
    archived      INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS holdings (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    ticker      TEXT DEFAULT '',
    asset_class TEXT NOT NULL DEFAULT 'Акции',
    currency    TEXT NOT NULL DEFAULT 'RUB',
    quantity    REAL NOT NULL DEFAULT 0,
    avg_price   REAL NOT NULL DEFAULT 0,
    price       REAL NOT NULL DEFAULT 0,
    updated_at  TEXT DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS cashflows (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('deposit','withdrawal','income','expense')),
    amount      REAL NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'RUB',
    category    TEXT DEFAULT '',           -- Дивиденды, Стейкинг, Проект…
    source      TEXT DEFAULT '',           -- бумага / монета / протокол / клиент
    pending     INTEGER DEFAULT 0,         -- 1 = доход ожидается, ещё не получен
    note        TEXT DEFAULT '',
    rent_payment_id INTEGER                -- создана автоматически из платежа по аренде
);
CREATE TABLE IF NOT EXISTS leases (       -- договоры аренды
    id             INTEGER PRIMARY KEY,
    account_id     INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tenant         TEXT NOT NULL,
    contact        TEXT DEFAULT '',
    start_date     TEXT NOT NULL,
    end_date       TEXT DEFAULT '',        -- пусто = бессрочно / действует
    rent_amount    REAL NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL DEFAULT 'RUB',
    frequency      TEXT NOT NULL DEFAULT 'month',  -- month | quarter | halfyear | year | day
    pay_day        INTEGER DEFAULT 0,      -- число месяца для оплаты (0 = в начале периода)
    deposit_amount REAL DEFAULT 0,
    deposit_currency TEXT DEFAULT 'RUB',
    agent          TEXT DEFAULT '',        -- риелтор / управляющий; пусто = напрямую
    agent_fee      TEXT DEFAULT '',        -- условия вознаграждения риелтора
    utilities      TEXT DEFAULT '',        -- кто платит коммуналку и т.п.
    terms          TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rent_payments (
    id               INTEGER PRIMARY KEY,
    lease_id         INTEGER NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    period_start     TEXT NOT NULL,
    period_end       TEXT NOT NULL,
    due_date         TEXT NOT NULL,
    amount           REAL NOT NULL,        -- сколько должен арендатор (в валюте договора)
    currency         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'expected',  -- expected | agent | received
    tenant_paid_date TEXT DEFAULT '',      -- когда арендатор заплатил (мне или риелтору)
    received_date    TEXT DEFAULT '',      -- когда деньги дошли до меня
    received_amount  REAL DEFAULT 0,       -- сколько я получил фактически
    received_currency TEXT DEFAULT 'RUB',
    cb_rate          REAL DEFAULT 0,       -- курс ЦБ на дату, ₽ за 1 ед. валюты договора (для сравнения)
    note             TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rates (
    currency   TEXT PRIMARY KEY,
    rate       REAL NOT NULL,       -- сколько RUB стоит 1 единица валюты
    updated_at TEXT DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS snapshots (
    date       TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    value_rub  REAL NOT NULL,
    invested_rub REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (date, account_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_RATES = {"RUB": 1.0, "USD": 82.0, "EUR": 95.0, "CNY": 11.4}

_vault = None


def set_vault(v):
    global _vault
    _vault = v


def get_db():
    if _vault is None or not _vault.unlocked:
        raise RuntimeError("Хранилище заблокировано")
    return _vault.con


def close_db(_exc=None):
    pass


def _columns(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def migrate(con):
    """Обновляет базу, созданную прошлой версией приложения, без потери данных."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "accounts" in tables:
        acc_sql = con.execute("SELECT sql FROM sqlite_master WHERE name='accounts'").fetchone()["sql"]
        if "CHECK" in acc_sql:
            # v1 ограничивал типы счетов — пересобираем таблицу без ограничения
            con.execute("PRAGMA foreign_keys = OFF")
            con.executescript("""
                BEGIN;
                CREATE TABLE accounts_new (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    institution TEXT DEFAULT '', currency TEXT NOT NULL DEFAULT 'RUB',
                    current_value REAL DEFAULT 0, tax_rate REAL DEFAULT 0, note TEXT DEFAULT '',
                    archived INTEGER DEFAULT 0, created_at TEXT DEFAULT (date('now')));
                INSERT INTO accounts_new(id, name, kind, institution, currency, current_value, note, archived, created_at)
                    SELECT id, name, kind, institution, currency, current_value, note, archived, created_at FROM accounts;
                DROP TABLE accounts;
                ALTER TABLE accounts_new RENAME TO accounts;
                COMMIT;
            """)
            con.execute("PRAGMA foreign_keys = ON")
        elif "tax_rate" not in _columns(con, "accounts"):
            con.execute("ALTER TABLE accounts ADD COLUMN tax_rate REAL DEFAULT 0")
    if "cashflows" in tables:
        cols = _columns(con, "cashflows")
        for col, ddl in (("category", "TEXT DEFAULT ''"), ("source", "TEXT DEFAULT ''"), ("pending", "INTEGER DEFAULT 0"),
                         ("rent_payment_id", "INTEGER")):
            if col not in cols:
                con.execute(f"ALTER TABLE cashflows ADD COLUMN {col} {ddl}")
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()


def init_db(con):
    con.execute("PRAGMA foreign_keys = ON")
    migrate(con)
    con.executescript(SCHEMA)
    for cur, rate in DEFAULT_RATES.items():
        con.execute("INSERT OR IGNORE INTO rates(currency, rate) VALUES (?, ?)", (cur, rate))
    con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('base_currency', 'RUB')")
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()
