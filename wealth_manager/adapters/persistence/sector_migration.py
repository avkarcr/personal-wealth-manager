"""Schema 19 -> 20. No portfolio, price or classification records are changed."""
SCHEMA = """
CREATE TABLE sector_configs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE,
 config TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE sector_active (
 id INTEGER PRIMARY KEY CHECK(id=1), config_id INTEGER NOT NULL REFERENCES sector_configs(id)
);
CREATE TABLE sector_revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, sector_id INTEGER NOT NULL REFERENCES sectors(id) ON DELETE RESTRICT,
 metric TEXT NOT NULL, period_end TEXT NOT NULL, published_at TEXT NOT NULL, received_at TEXT NOT NULL,
 supersedes_id INTEGER REFERENCES sector_revisions(id), payload TEXT NOT NULL
);
CREATE INDEX sector_revision_lookup ON sector_revisions(sector_id,metric,period_end,id);
CREATE TABLE sector_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, version TEXT NOT NULL,
 as_of TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('known','latest')),
 config_id INTEGER NOT NULL REFERENCES sector_configs(id), result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE sector_run_inputs (
 run_id INTEGER NOT NULL REFERENCES sector_runs(id), revision_id INTEGER NOT NULL REFERENCES sector_revisions(id),
 PRIMARY KEY(run_id,revision_id)
);
CREATE TABLE sector_run_macro_inputs (
 run_id INTEGER NOT NULL REFERENCES sector_runs(id), revision_id INTEGER NOT NULL REFERENCES macro_revisions(id),
 PRIMARY KEY(run_id,revision_id)
);
"""


def migrate(connection):
    connection.executescript('BEGIN IMMEDIATE;'+SCHEMA)
    for table in ('sector_configs','sector_revisions','sector_runs','sector_run_inputs','sector_run_macro_inputs'):
        for action in ('UPDATE','DELETE'):
            connection.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable sector evidence'); END")
    connection.execute('PRAGMA user_version=20');connection.commit()
