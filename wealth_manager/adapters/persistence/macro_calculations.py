"""Immutable calculation runs, separate from source observations."""
import json
from wealth_manager.modules.macroeconomics.calculations import canonical
from wealth_manager.shared.errors import EntityNotFound

class Repository:
    def __init__(self,con):self.con=con
    def recent(self):
        return [dict(r) for r in self.con.execute('SELECT id,created_at,as_of,mode,version FROM macro_calculations ORDER BY id DESC LIMIT 20')]
    def get(self,rid):
        row=self.con.execute('SELECT result FROM macro_calculations WHERE id=?',(rid,)).fetchone()
        if not row:raise EntityNotFound(rid)
        return json.loads(row[0])
    def save(self,fingerprint,result,created):
        self.con.execute('INSERT OR IGNORE INTO macro_calculations(fingerprint,version,as_of,mode,config,result,created_at) VALUES (?,?,?,?,?,?,?)',
            (fingerprint,result['version'],result['as_of'],result['mode'],canonical(result['config']),canonical(result),created))
        rid=self.con.execute('SELECT id FROM macro_calculations WHERE fingerprint=?',(fingerprint,)).fetchone()[0]
        for revision in result['inputs']:
            self.con.execute('INSERT OR IGNORE INTO macro_calculation_inputs(run_id,revision_id) VALUES (?,?)',(rid,revision))
        return rid
