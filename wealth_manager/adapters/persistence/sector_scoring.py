"""Append-only input revisions and frozen results in the existing encrypted vault."""
import json
from wealth_manager.modules.economic_cycle.application import digest
from wealth_manager.modules.macroeconomics.calculations import canonical
from wealth_manager.modules.sector_scoring.config import defaults, validate
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.shared.records import RecordError


class Repository:
    def __init__(self, connection): self.con=connection

    def state(self):
        row=self.con.execute('SELECT c.id,c.config FROM sector_active a JOIN sector_configs c ON c.id=a.config_id WHERE a.id=1').fetchone()
        return dict(config_id=row[0] if row else 0,config=json.loads(row[1]) if row else validate(defaults()))

    def sectors(self): return [dict(r) for r in self.con.execute('SELECT * FROM sectors ORDER BY id')]
    def revision_token(self): return self.con.execute('SELECT COALESCE(MAX(id),0) FROM sector_revisions').fetchone()[0]

    def observations(self):
        return [dict(json.loads(r['payload']),id=r['id'],received_at=r['received_at'],supersedes_id=r['supersedes_id'])
                for r in self.con.execute('SELECT * FROM sector_revisions ORDER BY id')]

    def observe(self, payload, received):
        previous=self.con.execute('SELECT * FROM sector_revisions WHERE sector_id=? AND metric=? AND period_end=? ORDER BY id DESC LIMIT 1',
                                  (payload['sector_id'],payload['metric'],payload['period_end'])).fetchone()
        encoded=canonical(payload)
        if previous and previous['payload']==encoded: return previous['id']
        if previous and payload['published_at'] and payload['published_at']<(previous['published_at'] or previous['received_at']):
            raise RecordError('Дата публикации исправления не может предшествовать предыдущей версии')
        return self.con.execute('INSERT INTO sector_revisions(sector_id,metric,period_end,published_at,received_at,supersedes_id,payload) VALUES (?,?,?,?,?,?,?)',
                                (payload['sector_id'],payload['metric'],payload['period_end'],payload['published_at'],received,previous['id'] if previous else None,encoded)).lastrowid

    def configure(self, config, created):
        fingerprint=digest(config)
        self.con.execute('INSERT OR IGNORE INTO sector_configs(fingerprint,config,created_at) VALUES (?,?,?)',(fingerprint,canonical(config),created))
        cid=self.con.execute('SELECT id FROM sector_configs WHERE fingerprint=?',(fingerprint,)).fetchone()[0]
        self.con.execute('INSERT INTO sector_active(id,config_id) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET config_id=excluded.config_id',(cid,))
        return cid

    def recent(self):
        return [dict(r) for r in self.con.execute('SELECT id,as_of,mode,created_at FROM sector_runs ORDER BY id DESC LIMIT 20')]

    def get(self,rid):
        row=self.con.execute('SELECT result FROM sector_runs WHERE id=?',(rid,)).fetchone()
        if not row: raise EntityNotFound(rid)
        return json.loads(row[0])

    def save(self,fingerprint,result,created):
        cid=self.configure(result['config'],created)
        self.con.execute('INSERT OR IGNORE INTO sector_runs(fingerprint,version,as_of,mode,config_id,result,created_at) VALUES (?,?,?,?,?,?,?)',
                         (fingerprint,result['version'],result['as_of'],result['mode'],cid,canonical(result),created))
        rid=self.con.execute('SELECT id FROM sector_runs WHERE fingerprint=?',(fingerprint,)).fetchone()[0]
        self.con.executemany('INSERT OR IGNORE INTO sector_run_inputs(run_id,revision_id) VALUES (?,?)',[(rid,r) for r in result['inputs']])
        self.con.executemany('INSERT OR IGNORE INTO sector_run_macro_inputs(run_id,revision_id) VALUES (?,?)',[(rid,r) for r in result['cycle']['inputs']])
        return rid
