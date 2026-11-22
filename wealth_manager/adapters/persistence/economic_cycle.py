"""Append-only cycle configurations and runs inside the encrypted vault."""
import json
from wealth_manager.modules.economic_cycle.application import digest
from wealth_manager.modules.economic_cycle.config import defaults, validate
from wealth_manager.modules.macroeconomics.calculations import canonical
from wealth_manager.shared.errors import EntityNotFound


class Repository:
    def __init__(self,con): self.con=con
    def state(self):
        row=self.con.execute('SELECT c.id,c.config FROM economic_cycle_active a JOIN economic_cycle_configs c ON c.id=a.config_id WHERE a.id=1').fetchone()
        return dict(config_id=row[0] if row else 0, config=json.loads(row[1]) if row else validate(defaults()))
    def configure(self,config,created):
        fingerprint=digest(config)
        self.con.execute('INSERT OR IGNORE INTO economic_cycle_configs(fingerprint,config,created_at) VALUES (?,?,?)',(fingerprint,canonical(config),created))
        cid=self.con.execute('SELECT id FROM economic_cycle_configs WHERE fingerprint=?',(fingerprint,)).fetchone()[0]
        self.con.execute('INSERT INTO economic_cycle_active(id,config_id) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET config_id=excluded.config_id',(cid,))
        return cid
    def recent(self):
        return [dict(r) for r in self.con.execute('SELECT id,as_of,mode,version,phase,created_at FROM economic_cycle_runs ORDER BY id DESC LIMIT 20')]
    def get(self,rid):
        row=self.con.execute('SELECT result FROM economic_cycle_runs WHERE id=?',(rid,)).fetchone()
        if not row: raise EntityNotFound(rid)
        return json.loads(row[0])
    def save(self,fingerprint,result,created):
        cid=self.configure(result['config'],created)
        self.con.execute('INSERT OR IGNORE INTO economic_cycle_runs(fingerprint,version,as_of,mode,phase,config_id,result,created_at) VALUES (?,?,?,?,?,?,?,?)',
                         (fingerprint,result['version'],result['as_of'],result['mode'],result['phase'],cid,canonical(result),created))
        rid=self.con.execute('SELECT id FROM economic_cycle_runs WHERE fingerprint=?',(fingerprint,)).fetchone()[0]
        self.con.executemany('INSERT OR IGNORE INTO economic_cycle_inputs(run_id,revision_id) VALUES (?,?)',[(rid,r) for r in result['inputs']])
        return rid
