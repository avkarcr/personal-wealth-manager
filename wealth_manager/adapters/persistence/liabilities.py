from dataclasses import asdict
from .operations import get_row

class SqliteLiabilities:
    def __init__(self,connection):self.connection=connection
    def get(self,lid):return get_row(self.connection,'liabilities',lid)
    def payments(self):return [dict(r) for r in self.connection.execute('SELECT * FROM liability_payments ORDER BY date,id')]
    def payment(self,pid):return get_row(self.connection,'liability_payments',pid)
    def find_debt(self,key):return self._find("liabilities",key)
    def find_payment(self,key):return self._find("liability_payments",key)
    def _find(self,kind,key):
        r=self.connection.execute('SELECT * FROM '+kind+' WHERE request_id=?',(key,)).fetchone()
        return dict(r) if r else None
    def insert(self,kind,record):
        data=asdict(record)
        return self.connection.execute('INSERT INTO '+kind+' ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',tuple(data.values())).lastrowid
    def create(self,record):return self.insert('liabilities',record)
    def pay(self,record):return self.insert('liability_payments',record)
    def cancel_debt(self,lid):self._void("liabilities",lid)
    def cancel_payment(self,pid):self._void("liability_payments",pid)
    def _void(self,kind,rid):self.connection.execute('UPDATE '+kind+' SET voided=1 WHERE id=?',(rid,))
    def link(self,pid,eid):self.connection.execute('INSERT INTO liability_entries VALUES (?,?)',(pid,eid))
    def entries(self,pid):return [r[0] for r in self.connection.execute('SELECT entry_id FROM liability_entries WHERE payment_id=?',(pid,))]
    def owns_entry(self,eid):return self.connection.execute('SELECT 1 FROM liability_entries WHERE entry_id=?',(eid,)).fetchone() is not None
    def linked_asset(self,aid):return self.connection.execute('SELECT 1 FROM liabilities WHERE asset_id=?',(aid,)).fetchone() is not None
