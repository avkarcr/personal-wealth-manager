"""Schema 20 -> 21: one ISS snapshot per existing listing."""

SCHEMA = """
CREATE TABLE listing_iss_snapshots (
    listing_id INTEGER PRIMARY KEY REFERENCES security_listings(id) ON DELETE CASCADE,
    isin TEXT NOT NULL,
    registration_number TEXT NOT NULL,
    name TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    access TEXT NOT NULL CHECK(access IN ('familiarization', 'agreement')),
    day TEXT,
    close TEXT,
    basis TEXT CHECK(basis IN ('previous', 'candle')),
    change TEXT,
    turnover TEXT,
    previous_day TEXT,
    CHECK((day IS NULL AND close IS NULL AND basis IS NULL AND change IS NULL AND turnover IS NULL)
        OR (day IS NOT NULL AND close IS NOT NULL AND basis IS NOT NULL)),
    CHECK(basis <> 'previous' OR (change IS NULL AND turnover IS NULL)),
    CHECK((change IS NULL AND previous_day IS NULL)
        OR (change IS NOT NULL AND previous_day IS NOT NULL AND previous_day < day))
);
"""


def migrate(connection):
    connection.executescript('BEGIN IMMEDIATE;' + SCHEMA)
    connection.execute('PRAGMA user_version=21')
    connection.commit()
