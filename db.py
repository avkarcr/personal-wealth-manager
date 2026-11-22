"""Схема базы и миграции. Сама база хранится в зашифрованном виде — см. vault.py."""
SCHEMA_VERSION = 17
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
        con.execute('PRAGMA user_version=4')
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
            if version < 4:
                _init_legacy(con)
                _migrate_entities(con)
            if version < 5: _migrate_planning(con)
            if version < 6: _migrate_research(con)
            if version < 7: _migrate_classifications(con)
            if version < 8: _migrate_valuation_dates(con)
            if version < 9: _migrate_history(con)
            if version < 10: _migrate_decimals(con)
            if version < 11: _migrate_ledger(con)
            if version < 12: _migrate_liabilities(con)
            if version < 13: _migrate_tax_documents(con)
            if version < 14: _migrate_goal_forecasts(con)
            if version < 15: _migrate_instruments(con)
            if version < 16: _migrate_macro_versions(con)
            if version < 17: _migrate_macro_calculations(con)
        except BaseException:
            con.rollback()
            before.backup(con)
            raise
        finally:
            before.close()
    con.execute('PRAGMA foreign_keys=ON')
    from wealth_manager.adapters.persistence.decimals import configure
    configure(con)


PLANNING_SCHEMA = """
CREATE TABLE goals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 target_amount TEXT NOT NULL,
 currency TEXT NOT NULL,
 due_date TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE portfolios (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
 note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE allocations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
 holding_id INTEGER REFERENCES holdings(id) ON DELETE RESTRICT,
 asset_id INTEGER REFERENCES assets(id) ON DELETE RESTRICT,
 share_bps INTEGER NOT NULL CHECK(typeof(share_bps)='integer' AND share_bps>0 AND share_bps<=10000),
 CHECK((holding_id IS NOT NULL AND asset_id IS NULL) OR (holding_id IS NULL AND asset_id IS NOT NULL))
);
CREATE UNIQUE INDEX allocation_holding ON allocations(portfolio_id,holding_id) WHERE holding_id IS NOT NULL;
CREATE UNIQUE INDEX allocation_asset ON allocations(portfolio_id,asset_id) WHERE asset_id IS NOT NULL;
"""


def _migrate_planning(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        for statement in _statements(PLANNING_SCHEMA):con.execute(statement)
        for operation in ('INSERT','UPDATE'):
            exclusion = ' AND id<>OLD.id' if operation=='UPDATE' else ''
            con.execute(f"""CREATE TRIGGER allocation_limit_{operation.lower()} BEFORE {operation} ON allocations
                WHEN NEW.share_bps + COALESCE((SELECT SUM(share_bps) FROM allocations
                WHERE ((NEW.holding_id IS NOT NULL AND holding_id=NEW.holding_id)
                    OR (NEW.asset_id IS NOT NULL AND asset_id=NEW.asset_id)){exclusion}),0)>10000
                BEGIN SELECT RAISE(ABORT,'Allocation exceeds 100 percent'); END""")
        con.execute('PRAGMA user_version=5')
        con.commit()
    except BaseException:
        con.rollback()
        raise


RESEARCH_SCHEMA = """
CREATE TABLE companies (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 ticker TEXT NOT NULL DEFAULT '',
 exchange TEXT NOT NULL DEFAULT '',
 country TEXT NOT NULL,
 sector TEXT NOT NULL DEFAULT '',
 website TEXT NOT NULL DEFAULT '',
 thesis TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('watch','owned','archived'))
);
CREATE UNIQUE INDEX company_listing ON companies(ticker COLLATE NOCASE,exchange COLLATE NOCASE) WHERE ticker<>'';
CREATE TABLE macro_observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 country TEXT NOT NULL,
 indicator TEXT NOT NULL CHECK(indicator IN ('key_rate','refinancing_rate','cpi_yoy','gdp_yoy','yield_2y','yield_10y')),
 date TEXT NOT NULL,
 value TEXT NOT NULL,
 source TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 UNIQUE(country,indicator,date)
);
CREATE TABLE tax_entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 year INTEGER NOT NULL CHECK(year BETWEEN 1900 AND 9999),
 country TEXT NOT NULL,
 category TEXT NOT NULL,
 currency TEXT NOT NULL,
 base TEXT NOT NULL,
 rate TEXT NOT NULL,
 paid TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE tax_deductions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 year INTEGER NOT NULL CHECK(year BETWEEN 1900 AND 9999),
 country TEXT NOT NULL,
 category TEXT NOT NULL,
 currency TEXT NOT NULL,
 amount TEXT NOT NULL,
 refund TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('collecting','ready','submitted','received','rejected')),
 documents TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT ''
);
"""


def _migrate_research(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        for statement in _statements(RESEARCH_SCHEMA): con.execute(statement)
        con.execute('PRAGMA user_version=6')
        con.commit()
    except BaseException:
        con.rollback()
        raise


# Frozen migration mapping: future display labels must not change migration behavior.
ASSET_CODES_V7 = {'Акции': 'stocks', 'Облигации': 'bonds', 'Фонды': 'funds', 'Деньги': 'cash', 'Золото': 'gold', 'Крипто': 'crypto', 'Стейблкоины': 'stablecoins', 'Прочее': 'other'}
CATEGORY_CODES_V7 = {'Дивиденды': 'dividends', 'Купоны': 'coupons', 'Проценты на остаток': 'cash_interest', 'Налоговый вычет (ИИС)': 'iis_deduction', 'Комиссия брокера': 'broker_fee', 'Налог': 'tax', 'Обслуживание счёта': 'account_service', 'Стейкинг': 'staking', 'Фарминг / LP': 'farming', 'Аирдроп': 'airdrop', 'Лаунчпул': 'launchpool', 'Earn / сбережения': 'earn', 'P2P': 'p2p', 'Трейдинг': 'trading', 'Рефералы': 'referrals', 'Ноды / валидатор': 'nodes', 'Комиссия биржи': 'exchange_fee', 'Газ (комиссия сети)': 'gas', 'Комиссия P2P': 'p2p_fee', 'Аренда': 'rent', 'Посуточная аренда': 'daily_rent', 'Коммунальные платежи': 'utilities', 'Ремонт': 'repairs', 'Ипотека — проценты': 'mortgage_interest', 'Страховка': 'insurance', 'Проценты': 'interest', 'Доход': 'income', 'Комиссия': 'fee', 'Проект': 'project', 'Почасовая работа': 'hourly_work', 'Абонентская оплата': 'subscription', 'Бонус': 'bonus', 'Консультация': 'consulting', 'Комиссия площадки': 'platform_fee', 'Налог (НПД)': 'npd_tax', 'Софт и сервисы': 'software', 'Вывод / конвертация': 'conversion'}


def _migrate_classifications(con):
    con.execute('PRAGMA foreign_keys=OFF')
    con.execute('BEGIN IMMEDIATE')
    try:
        con.execute("ALTER TABLE holdings ADD COLUMN asset_class_code TEXT NOT NULL DEFAULT 'stocks'")
        con.execute("UPDATE holdings SET asset_class_code='custom'")
        for label,code in ASSET_CODES_V7.items():
            con.execute("UPDATE holdings SET asset_class_code=?,asset_class='' WHERE asset_class=?",(code,label))
        # Rebuild only holdings to replace its legacy SQL default. IDs and referencing FKs stay intact.
        rows=con.execute('SELECT * FROM holdings').fetchall()
        schema=con.execute("SELECT sql FROM sqlite_master WHERE name='holdings'").fetchone()[0]
        extras=con.execute("SELECT sql FROM sqlite_master WHERE tbl_name='holdings' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
        con.execute('DROP TABLE holdings')
        con.execute(schema.replace("DEFAULT 'Акции'", "DEFAULT ''"))
        if rows: con.executemany('INSERT INTO holdings VALUES ('+','.join('?' for _ in rows[0])+')',[tuple(r) for r in rows])
        for row in extras: con.execute(row[0])
        con.execute("ALTER TABLE cashflows ADD COLUMN category_code TEXT NOT NULL DEFAULT ''")
        for label,code in CATEGORY_CODES_V7.items():
            con.execute("UPDATE cashflows SET category_code=?,category='' WHERE category=?",(code,label))
        if con.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('Classification migration broke links')
        con.execute('PRAGMA user_version=7')
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.execute('PRAGMA foreign_keys=ON')


def _migrate_valuation_dates(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        # Existing property estimates have no known valuation date; never invent one.
        con.execute("ALTER TABLE assets ADD COLUMN valued_at TEXT NOT NULL DEFAULT ''")
        con.execute('PRAGMA user_version=8')
        con.commit()
    except BaseException:
        con.rollback()
        raise


def _migrate_history(con):
    con.execute("ALTER TABLE entities ADD COLUMN closed_at TEXT NOT NULL DEFAULT ''")
    view = con.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name='accounts'").fetchone()[0]
    con.execute('DROP VIEW accounts')
    con.execute(view.replace('e.created_at', 'e.created_at,e.closed_at'))
    con.execute("CREATE TABLE historical_rates (date TEXT NOT NULL, currency TEXT NOT NULL, rate REAL NOT NULL CHECK(rate>0), PRIMARY KEY(date,currency))")
    con.execute("CREATE TABLE account_status_events (account_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE, date TEXT NOT NULL, closed INTEGER NOT NULL CHECK(closed IN (0,1)), PRIMARY KEY(account_id,date))")
    # Retain only the known observation date; never manufacture earlier rates.
    con.execute("INSERT INTO historical_rates SELECT updated_at,currency,rate FROM rates WHERE rate>0 AND length(updated_at)=10")
    con.execute('PRAGMA user_version=9')
    con.commit()


def _migrate_decimals(con):
    import re
    from wealth_manager.shared.decimal import exact_text
    tables = ('assets','income_sources','holdings','cashflows','leases','rent_payments','rates','historical_rates','snapshots')
    con.execute('PRAGMA foreign_keys=OFF')
    con.execute('BEGIN IMMEDIATE')
    try:
        views = con.execute("SELECT sql FROM sqlite_master WHERE type='view'").fetchall()
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall():
            con.execute('DROP VIEW "'+row[0]+'"')
        for table in tables:
            columns = con.execute('PRAGMA table_info('+table+')').fetchall()
            numeric = {i for i,c in enumerate(columns) if c[2].upper()=='REAL'}
            rows = con.execute('SELECT * FROM '+table).fetchall()
            schema = con.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]
            extras = con.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",(table,)).fetchall()
            sequence = con.execute('SELECT seq FROM sqlite_sequence WHERE name=?',(table,)).fetchone()
            con.execute('DROP TABLE '+table)
            con.execute(re.sub(r'\bREAL\b','DECIMAL_TEXT',schema,flags=re.I))
            if rows:
                values=[tuple(exact_text(v) if i in numeric and v is not None else v for i,v in enumerate(r)) for r in rows]
                con.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in columns)+')',values)
            for extra in extras: con.execute(extra[0])
            if sequence:
                con.execute('DELETE FROM sqlite_sequence WHERE name=?',(table,))
                con.execute('INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)',(table,sequence[0]))
        for view in views:con.execute(view[0])
        if con.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Decimal migration broke links')
        con.execute('PRAGMA user_version=10')
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.execute('PRAGMA foreign_keys=ON')


def _migrate_ledger(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        for statement in _statements(LEDGER_SCHEMA):con.execute(statement)
        con.execute('PRAGMA user_version=11');con.commit()
    except BaseException:
        con.rollback();raise

LEDGER_SCHEMA = """
CREATE TABLE ledger_accounts (
 account_id INTEGER PRIMARY KEY REFERENCES financial_accounts(id),
 started_on TEXT NOT NULL,
 baseline_rub DECIMAL_TEXT NOT NULL
);
CREATE TABLE ledger_openings (
 holding_id INTEGER PRIMARY KEY REFERENCES holdings(id),
 quantity DECIMAL_TEXT NOT NULL, cost DECIMAL_TEXT NOT NULL
);
CREATE TABLE ledger_entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 date TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('buy','sell','deposit','withdrawal','income','expense','transfer')),
 holding_id INTEGER NOT NULL REFERENCES holdings(id),
 quantity DECIMAL_TEXT NOT NULL, price DECIMAL_TEXT NOT NULL, fee DECIMAL_TEXT NOT NULL,
 cash_id INTEGER REFERENCES holdings(id), target_id INTEGER REFERENCES holdings(id),
 note TEXT NOT NULL DEFAULT '', request_id TEXT NOT NULL UNIQUE,
 voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1)), voided_at TEXT NOT NULL DEFAULT '',
 realized DECIMAL_TEXT
);
CREATE INDEX ledger_order ON ledger_entries(date,id);
CREATE TABLE ledger_invalid_snapshots (
 account_id INTEGER NOT NULL REFERENCES entities(id), date TEXT NOT NULL, PRIMARY KEY(account_id,date)
);
CREATE TABLE ledger_flows (
 entry_id INTEGER NOT NULL REFERENCES ledger_entries(id),
 flow_id INTEGER PRIMARY KEY REFERENCES cashflows(id) ON DELETE CASCADE
);
"""


def _migrate_liabilities(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        for statement in _statements(LIABILITY_SCHEMA):con.execute(statement)
        con.execute('PRAGMA user_version=12');con.commit()
    except BaseException:
        con.rollback();raise

LIABILITY_SCHEMA = """
CREATE TABLE liabilities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL, opening_date TEXT NOT NULL, opening_principal DECIMAL_TEXT NOT NULL,
 currency TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('loan','mortgage','deposit')),
 asset_id INTEGER REFERENCES assets(id) ON DELETE RESTRICT,
 creditor TEXT NOT NULL, due_date TEXT NOT NULL, note TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE, voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1))
);
CREATE TABLE liability_payments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 liability_id INTEGER NOT NULL REFERENCES liabilities(id), date TEXT NOT NULL,
 principal DECIMAL_TEXT NOT NULL, interest DECIMAL_TEXT NOT NULL, fee DECIMAL_TEXT NOT NULL,
 cash_id INTEGER REFERENCES holdings(id), note TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE, voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1))
);
CREATE TABLE liability_entries (
 payment_id INTEGER NOT NULL REFERENCES liability_payments(id),
 entry_id INTEGER PRIMARY KEY REFERENCES ledger_entries(id)
);
"""


def _migrate_tax_documents(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        con.execute("ALTER TABLE tax_deductions ADD COLUMN actual_refund DECIMAL_TEXT NOT NULL DEFAULT '0'")
        con.execute("ALTER TABLE tax_deductions ADD COLUMN received_date TEXT NOT NULL DEFAULT ''")
        con.execute("UPDATE tax_deductions SET actual_refund=refund WHERE status='received'")
        for statement in _statements(TAX_DOCUMENT_SCHEMA):con.execute(statement)
        con.execute('PRAGMA user_version=13');con.commit()
    except BaseException:
        con.rollback();raise

TAX_DOCUMENT_SCHEMA = """
CREATE TABLE tax_payments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 entry_id INTEGER NOT NULL REFERENCES tax_entries(id) ON DELETE RESTRICT,
 date TEXT NOT NULL, amount DECIMAL_TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('paid','withheld')), note TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE, voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1))
);
CREATE TABLE tax_documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 entry_id INTEGER REFERENCES tax_entries(id) ON DELETE RESTRICT,
 deduction_id INTEGER REFERENCES tax_deductions(id) ON DELETE RESTRICT,
 filename TEXT NOT NULL, mime TEXT NOT NULL, digest TEXT NOT NULL,
 size INTEGER NOT NULL, content BLOB NOT NULL,
 date TEXT NOT NULL, number TEXT NOT NULL, issuer TEXT NOT NULL, note TEXT NOT NULL,
 CHECK((entry_id IS NULL) != (deduction_id IS NULL))
);
CREATE UNIQUE INDEX tax_document_entry ON tax_documents(entry_id,digest) WHERE entry_id IS NOT NULL;
CREATE UNIQUE INDEX tax_document_deduction ON tax_documents(deduction_id,digest) WHERE deduction_id IS NOT NULL;
"""


def _migrate_goal_forecasts(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        for column in ('monthly_contribution','annual_return','annual_inflation'):
            con.execute("ALTER TABLE goals ADD COLUMN "+column+" DECIMAL_TEXT NOT NULL DEFAULT '0'")
        con.execute("ALTER TABLE goals ADD COLUMN contribution_step DECIMAL_TEXT NOT NULL DEFAULT '0.01'")
        con.execute('PRAGMA user_version=14');con.commit()
    except BaseException:
        con.rollback();raise


def _migrate_instruments(con):
    con.executescript("""
BEGIN IMMEDIATE;
CREATE TABLE crypto_assets (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, symbol TEXT NOT NULL,
 network TEXT NOT NULL DEFAULT '', contract TEXT NOT NULL DEFAULT '',
 website TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX crypto_identity ON crypto_assets(symbol COLLATE NOCASE,network COLLATE NOCASE,contract);
CREATE TABLE holding_links (
 holding_id INTEGER PRIMARY KEY REFERENCES holdings(id) ON DELETE CASCADE,
 company_id INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
 crypto_id INTEGER REFERENCES crypto_assets(id) ON DELETE RESTRICT,
 CHECK((company_id IS NULL) != (crypto_id IS NULL))
);
CREATE INDEX holding_company ON holding_links(company_id);
CREATE INDEX holding_crypto ON holding_links(crypto_id);
PRAGMA user_version=15;
COMMIT;
""")


MACRO_VERSION_SCHEMA = """
CREATE TABLE macro_sources (
 id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, kind TEXT NOT NULL,
 url TEXT NOT NULL DEFAULT '', terms TEXT NOT NULL DEFAULT ''
);
CREATE TABLE macro_indicators (code TEXT PRIMARY KEY, name_ru TEXT NOT NULL, name_en TEXT NOT NULL);
CREATE TABLE macro_series (
 id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE, source_id INTEGER NOT NULL REFERENCES macro_sources(id),
 indicator TEXT NOT NULL REFERENCES macro_indicators(code), country TEXT NOT NULL, provider_key TEXT NOT NULL,
 unit TEXT NOT NULL, frequency TEXT NOT NULL, scale TEXT NOT NULL, adjustment TEXT NOT NULL,
 base_year INTEGER, methodology TEXT NOT NULL
);
CREATE TABLE macro_artifacts (
 id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE, source_id INTEGER NOT NULL REFERENCES macro_sources(id),
 locator TEXT NOT NULL, digest TEXT NOT NULL, content BLOB NOT NULL CHECK(length(content)<=2000000),
 parser_version TEXT NOT NULL, media_type TEXT NOT NULL, received_at TEXT NOT NULL
);
CREATE TABLE macro_records (id INTEGER PRIMARY KEY AUTOINCREMENT, origin_key TEXT UNIQUE);
CREATE TABLE macro_revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL REFERENCES macro_records(id),
 series_id INTEGER NOT NULL REFERENCES macro_series(id), period_start TEXT NOT NULL, period_end TEXT NOT NULL,
 value TEXT NOT NULL, published_at TEXT NOT NULL, publication_precision TEXT NOT NULL,
 status TEXT NOT NULL, note TEXT NOT NULL, artifact_id INTEGER NOT NULL REFERENCES macro_artifacts(id),
 deleted INTEGER NOT NULL CHECK(deleted IN (0,1)), retrieved_at TEXT NOT NULL, available_at TEXT NOT NULL,
 fingerprint TEXT NOT NULL, previous_revision_id INTEGER REFERENCES macro_revisions(id),
 CHECK(period_start<=period_end)
);
CREATE INDEX macro_revision_record ON macro_revisions(record_id,id);
CREATE INDEX macro_revision_asof ON macro_revisions(available_at,record_id);
CREATE VIEW macro_observations AS
 SELECT r.record_id AS id,s.country,s.indicator,r.period_end AS date,r.value,a.locator AS source,r.note
 FROM macro_revisions r JOIN macro_series s ON s.id=r.series_id
 JOIN macro_sources src ON src.id=s.source_id JOIN macro_artifacts a ON a.id=r.artifact_id
 WHERE src.key='legacy_manual' AND r.deleted=0 AND r.id=(SELECT MAX(x.id) FROM macro_revisions x WHERE x.record_id=r.record_id);
CREATE TRIGGER macro_revision_no_update BEFORE UPDATE ON macro_revisions BEGIN SELECT RAISE(ABORT,'Immutable macro revision'); END;
CREATE TRIGGER macro_revision_no_delete BEFORE DELETE ON macro_revisions BEGIN SELECT RAISE(ABORT,'Immutable macro revision'); END;
CREATE TRIGGER macro_artifact_no_update BEFORE UPDATE ON macro_artifacts BEGIN SELECT RAISE(ABORT,'Immutable macro artifact'); END;
CREATE TRIGGER macro_artifact_no_delete BEFORE DELETE ON macro_artifacts BEGIN SELECT RAISE(ABORT,'Immutable macro artifact'); END;
"""

def _migrate_macro_versions(con):
    from wealth_manager.adapters.persistence.macro_data import Repository,canonical
    from wealth_manager.modules.macroeconomics.data import Indicator,Series,utcnow,INDICATOR_EN
    from wealth_manager.modules.macroeconomics.domain import INDICATORS
    rows=[dict(r) for r in con.execute('SELECT * FROM macro_observations ORDER BY id')]
    con.executescript('BEGIN IMMEDIATE; DROP TABLE macro_observations;'+MACRO_VERSION_SCHEMA)
    now=utcnow();repo=Repository(con)
    con.execute("INSERT INTO macro_sources(key,title,kind) VALUES ('legacy_manual','Manual observations','manual')")
    source_id=con.execute("SELECT id FROM macro_sources WHERE key='legacy_manual'").fetchone()[0]
    for row in rows:
        con.execute('INSERT INTO macro_records(id,origin_key) VALUES (?,NULL)',(row['id'],))
        sid=repo.series(source_id,Indicator(row['indicator'],INDICATORS[row['indicator']],INDICATOR_EN[row['indicator']]),Series(row['indicator'],row['country'],row['indicator'],'percent','unknown',methodology='legacy_unknown'))
        content=canonical(row).encode()
        aid=repo.artifact(source_id,row['source'],content,'migration-v16','application/json',now)
        repo.revision(row['id'],sid,row['date'],row['date'],row['value'],'','unknown','unknown',row['note'],aid,now)
    con.execute('PRAGMA user_version=16');con.commit()


MACRO_CALCULATION_SCHEMA = """
CREATE TABLE macro_calculations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE,
 version TEXT NOT NULL, as_of TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('known','latest')),
 config TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE macro_calculation_inputs (
 run_id INTEGER NOT NULL REFERENCES macro_calculations(id),
 revision_id INTEGER NOT NULL REFERENCES macro_revisions(id),
 PRIMARY KEY(run_id,revision_id)
);
CREATE TRIGGER macro_calculations_no_update BEFORE UPDATE ON macro_calculations BEGIN SELECT RAISE(ABORT,'Immutable calculation'); END;
CREATE TRIGGER macro_calculations_no_delete BEFORE DELETE ON macro_calculations BEGIN SELECT RAISE(ABORT,'Immutable calculation'); END;
CREATE TRIGGER macro_calculation_inputs_no_update BEFORE UPDATE ON macro_calculation_inputs BEGIN SELECT RAISE(ABORT,'Immutable calculation inputs'); END;
CREATE TRIGGER macro_calculation_inputs_no_delete BEFORE DELETE ON macro_calculation_inputs BEGIN SELECT RAISE(ABORT,'Immutable calculation inputs'); END;
"""


def _migrate_macro_calculations(con):
    con.executescript('BEGIN IMMEDIATE;'+MACRO_CALCULATION_SCHEMA)
    con.execute('PRAGMA user_version=17');con.commit()
