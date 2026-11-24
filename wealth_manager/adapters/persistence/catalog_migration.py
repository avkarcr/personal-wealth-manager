"""Frozen schema 18 -> 19 migration; no identity is inferred from a position ticker."""
SCHEMA = """
CREATE TABLE sectors (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL COLLATE NOCASE UNIQUE,
 name TEXT NOT NULL, name_en TEXT NOT NULL DEFAULT '',
 parent_id INTEGER REFERENCES sectors(id) ON DELETE RESTRICT,
 source_url TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
 CHECK(parent_id IS NULL OR parent_id<>id)
);
CREATE TABLE company_sectors (
 company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
 sector_id INTEGER NOT NULL REFERENCES sectors(id) ON DELETE RESTRICT,
 is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
 source TEXT NOT NULL DEFAULT '', as_of TEXT NOT NULL DEFAULT '', PRIMARY KEY(company_id,sector_id)
);
CREATE UNIQUE INDEX company_primary_sector ON company_sectors(company_id) WHERE is_primary=1;
CREATE TABLE securities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
 name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('unknown','common_share','preferred_share','bond','depositary_receipt','other')),
 isin TEXT NOT NULL DEFAULT '', registration_number TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT '',
 underlying_id INTEGER REFERENCES securities(id) ON DELETE RESTRICT,
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), source TEXT NOT NULL DEFAULT '', as_of TEXT NOT NULL DEFAULT '',
 UNIQUE(id,company_id), CHECK(underlying_id IS NULL OR (kind='depositary_receipt' AND underlying_id<>id))
);
CREATE UNIQUE INDEX security_isin ON securities(isin COLLATE NOCASE) WHERE isin<>'';
CREATE UNIQUE INDEX security_registration ON securities(company_id,registration_number COLLATE NOCASE) WHERE registration_number<>'';
CREATE TABLE security_listings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 security_id INTEGER NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
 exchange TEXT NOT NULL, ticker TEXT NOT NULL, board TEXT NOT NULL DEFAULT '',
 valid_from TEXT NOT NULL DEFAULT '', valid_to TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('unknown','active','suspended','delisted')),
 as_of TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
 CHECK(valid_from='' OR valid_to='' OR valid_to>=valid_from)
);
CREATE INDEX listing_identity ON security_listings(exchange COLLATE NOCASE,ticker COLLATE NOCASE,board COLLATE NOCASE);
CREATE TABLE listing_status_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL REFERENCES security_listings(id) ON DELETE CASCADE,
 status TEXT NOT NULL, as_of TEXT NOT NULL, source TEXT NOT NULL
);
CREATE TABLE holding_links_v19 (
 holding_id INTEGER PRIMARY KEY REFERENCES holdings(id) ON DELETE CASCADE,
 company_id INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
 crypto_id INTEGER REFERENCES crypto_assets(id) ON DELETE RESTRICT,
 security_id INTEGER,
 CHECK((company_id IS NULL) != (crypto_id IS NULL)),
 CHECK(security_id IS NULL OR company_id IS NOT NULL),
 FOREIGN KEY(security_id,company_id) REFERENCES securities(id,company_id) ON DELETE RESTRICT
);
INSERT INTO holding_links_v19(holding_id,company_id,crypto_id) SELECT holding_id,company_id,crypto_id FROM holding_links;
DROP TABLE holding_links;
ALTER TABLE holding_links_v19 RENAME TO holding_links;
CREATE INDEX holding_company ON holding_links(company_id);
CREATE INDEX holding_crypto ON holding_links(crypto_id);
CREATE INDEX holding_security ON holding_links(security_id);
DROP INDEX company_listing;
"""
# Project taxonomy v1: sector-level mapping, not official index membership.
# The reference factsheet is dated 2026-02-27. Names/links are frozen for this migration.
SOURCE='https://www.moex.com/files/4byz4s6bap1acvn8d12yc45cda'
SECTORS=(
 ('oil_gas','Нефть и газ','Oil and gas'),
 ('metals_mining','Металлы и добыча','Metals and mining'),
 ('electricity','Электроэнергетика','Electric utilities'),
 ('finance','Финансовый сектор','Financials'),
 ('telecom','Телекоммуникации','Telecommunications'),
 ('technology','Информационные технологии','Information technology'),
 ('consumer','Потребительский сектор','Consumer sector'),
 ('construction','Строительство','Construction'),
 ('transport','Транспорт','Transport'),
 ('chemicals','Химия и нефтехимия','Chemicals and petrochemicals'),
)


def migrate(connection):
    connection.executescript('BEGIN IMMEDIATE;'+SCHEMA)
    connection.executemany('INSERT INTO sectors(code,name,name_en,source_url) VALUES (?,?,?,?)',[(*r,SOURCE) for r in SECTORS])
    for cid,name,ticker,exchange in connection.execute("SELECT id,name,ticker,exchange FROM companies WHERE ticker<>'' AND exchange<>''").fetchall():
        sid=connection.execute("INSERT INTO securities(company_id,name,kind,source) VALUES (?,?,'unknown','Legacy company card')",(cid,name)).lastrowid
        lid=connection.execute("INSERT INTO security_listings(security_id,exchange,ticker,status,source) VALUES (?,?,?,'unknown','Legacy company card')",(sid,exchange,ticker)).lastrowid
        connection.execute("INSERT INTO listing_status_events(listing_id,status,as_of,source) VALUES (?,'unknown','','Legacy company card')",(lid,))
    if connection.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Catalog migration broke links')
    connection.execute('PRAGMA user_version=19');connection.commit()
