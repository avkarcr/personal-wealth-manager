"""Схема базы и миграции. Сама база хранится в зашифрованном виде — см. vault.py."""
SCHEMA_VERSION = 4
LEGACY_SCHEMA_VERSION = 3

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

def _columns(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def migrate(con):
    """Обновляет базу, созданную прошлой версией приложения, без потери данных."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version >= LEGACY_SCHEMA_VERSION:
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
    con.execute(f"PRAGMA user_version = {LEGACY_SCHEMA_VERSION}")
    con.commit()


def _init_legacy(con):
    con.execute("PRAGMA foreign_keys = ON")
    migrate(con)
    con.executescript(SCHEMA)
    for cur, rate in DEFAULT_RATES.items():
        con.execute("INSERT OR IGNORE INTO rates(currency, rate) VALUES (?, ?)", (cur, rate))
    con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('base_currency', 'RUB')")
    con.execute(f"PRAGMA user_version = {LEGACY_SCHEMA_VERSION}")
    con.commit()


ENTITY_SCHEMA = """
CREATE TABLE entities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 entity_type TEXT NOT NULL CHECK(entity_type IN ('account','asset','income_source')),
 name TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'RUB', note TEXT DEFAULT '',
 archived INTEGER DEFAULT 0, created_at TEXT DEFAULT (date('now'))
);
CREATE TABLE financial_accounts (
 id INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('broker','crypto','other')),
 institution TEXT DEFAULT ''
);
CREATE TABLE assets (
 id INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
 asset_type TEXT NOT NULL DEFAULT 'realty' CHECK(asset_type='realty'),
 current_value REAL DEFAULT 0, institution TEXT DEFAULT ''
);
CREATE TABLE income_sources (
 id INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
 source_type TEXT NOT NULL DEFAULT 'freelance' CHECK(source_type='freelance'),
 tax_rate REAL DEFAULT 0, institution TEXT DEFAULT ''
);
CREATE TABLE legacy_entity_map (
 legacy_account_id INTEGER PRIMARY KEY,
 entity_id INTEGER NOT NULL UNIQUE REFERENCES entities(id) ON DELETE CASCADE,
 entity_type TEXT NOT NULL
);
CREATE VIEW accounts AS
 SELECT e.id,e.name,f.kind,f.institution,e.currency,0.0 AS current_value,0.0 AS tax_rate,e.note,e.archived,e.created_at
 FROM entities e JOIN financial_accounts f ON f.id=e.id
 UNION ALL
 SELECT e.id,e.name,a.asset_type,a.institution,e.currency,a.current_value,0.0,e.note,e.archived,e.created_at
 FROM entities e JOIN assets a ON a.id=e.id
 UNION ALL
 SELECT e.id,e.name,i.source_type,i.institution,e.currency,0.0,i.tax_rate,e.note,e.archived,e.created_at
 FROM entities e JOIN income_sources i ON i.id=e.id ORDER BY 1;
"""


def _statements(script):
    # Schema scripts contain no semicolons inside string literals or triggers.
    return (statement.strip() for statement in script.split(';') if statement.strip())


def _migrate_entities(con):
    """Atomically replace the v3 owner table, retaining IDs and all dependent rows."""
    if con.in_transaction:
        raise RuntimeError('Migration requires a clean transaction boundary')
    con.execute('PRAGMA foreign_keys=OFF')
    try:
        con.execute('BEGIN IMMEDIATE')
        unknown = con.execute("SELECT id FROM accounts WHERE kind NOT IN ('broker','crypto','other','realty','freelance') LIMIT 1").fetchone()
        if unknown: raise ValueError('Unknown legacy entity kind; migration cancelled')
        invalid = con.execute("SELECT l.id FROM leases l JOIN accounts a ON a.id=l.account_id WHERE a.kind<>'realty' LIMIT 1").fetchone()
        if invalid: raise ValueError('Lease is not linked to real estate; migration cancelled')
        # Keep an exact, immutable compatibility archive including unusual old fields.
        con.execute('ALTER TABLE accounts RENAME TO legacy_accounts_v3')
        for statement in _statements(ENTITY_SCHEMA): con.execute(statement)
        con.execute("""INSERT INTO entities(id,entity_type,name,currency,note,archived,created_at)
          SELECT id,CASE kind WHEN 'realty' THEN 'asset' WHEN 'freelance' THEN 'income_source' ELSE 'account' END,
          name,currency,note,archived,created_at FROM legacy_accounts_v3""")
        con.execute("INSERT INTO financial_accounts SELECT id,kind,institution FROM legacy_accounts_v3 WHERE kind IN ('broker','crypto','other')")
        con.execute("INSERT INTO assets SELECT id,'realty',current_value,institution FROM legacy_accounts_v3 WHERE kind='realty'")
        con.execute("INSERT INTO income_sources SELECT id,'freelance',tax_rate,institution FROM legacy_accounts_v3 WHERE kind='freelance'")
        con.execute('INSERT INTO legacy_entity_map SELECT id,id,entity_type FROM entities')
        # Rebuild dependent tables so FKs point at real tables, never the compatibility view.
        for table in ('holdings','cashflows','leases','snapshots'):
            rows = con.execute('SELECT * FROM '+table).fetchall()
            columns = [r['name'] for r in con.execute('PRAGMA table_info('+table+')')]
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]
            con.execute('DROP TABLE '+table)
            sql = sql.replace('legacy_accounts_v3(id)','entities(id)').replace('accounts(id)','entities(id)')
            # SQLite quotes referenced names when ALTER TABLE rewrites foreign keys.
            sql = sql.replace('"legacy_accounts_v3"(id)','entities(id)')
            if table=='leases':
                sql = sql.replace('account_id','asset_id').replace('REFERENCES entities(id)','REFERENCES assets(id)')
                columns = ['asset_id' if c=='account_id' else c for c in columns]
            con.execute(sql)
            if rows:
                con.executemany('INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[tuple(r) for r in rows])
        for table,kind in (('financial_accounts','account'),('assets','asset'),('income_sources','income_source')):
            con.execute(f"CREATE TRIGGER guard_{table} BEFORE INSERT ON {table} WHEN (SELECT entity_type FROM entities WHERE id=NEW.id)<>'{kind}' BEGIN SELECT RAISE(ABORT,'Entity type mismatch'); END")
        violations = con.execute('PRAGMA foreign_key_check').fetchall()
        if violations: raise ValueError('Migration would break foreign key links')
        con.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.execute('PRAGMA foreign_keys=ON')


def init_db(con):
    import sqlite3
    version = con.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION: raise ValueError('Database was created by a newer application')
    if version < SCHEMA_VERSION:
        if con.in_transaction: raise RuntimeError('Migration requires a clean transaction boundary')
        before=sqlite3.connect(':memory:')
        con.backup(before)
        try:
            _init_legacy(con)
            _migrate_entities(con)
        except BaseException:
            con.rollback()
            before.backup(con)
            raise
        finally:
            before.close()
    con.execute('PRAGMA foreign_keys=ON')
