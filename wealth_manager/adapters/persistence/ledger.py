from dataclasses import asdict
from wealth_manager.shared.decimal import decimal, arithmetic
from wealth_manager.shared.errors import EntityNotFound

class SqliteLedgerRepository:
    def __init__(self, connection):self.connection=connection
    def enabled(self, aid):
        return self.connection.execute('SELECT 1 FROM ledger_accounts WHERE account_id=?',(aid,)).fetchone() is not None
    def data(self):
        con=self.connection
        accounts={r['id']:dict(r,ledger_started_on='',ledger_baseline_rub=0) for r in con.execute('SELECT * FROM accounts')}
        for r in con.execute('SELECT * FROM ledger_accounts'):
            accounts[r['account_id']].update(ledger_started_on=r['started_on'],ledger_baseline_rub=decimal(r['baseline_rub']))
        return dict(accounts=accounts,holdings={r['id']:dict(r) for r in con.execute('SELECT * FROM holdings')},
                    openings={r['holding_id']:dict(r) for r in con.execute('SELECT * FROM ledger_openings')},
                    entries=[dict(r) for r in con.execute('SELECT * FROM ledger_entries ORDER BY date,id')])
    @arithmetic
    def activate(self, aid, day, baseline):
        self.connection.execute('INSERT INTO ledger_accounts VALUES (?,?,?)',(aid,day,baseline))
        for r in self.connection.execute('SELECT * FROM holdings WHERE account_id=?',(aid,)).fetchall():self.register_position(r['id'])
    @arithmetic
    def register_position(self, hid):
        r=self.connection.execute('SELECT * FROM holdings WHERE id=?',(hid,)).fetchone()
        cost=r['quantity']*(1 if r['asset_class_code']=='cash' else r['avg_price'])
        self.connection.execute('INSERT INTO ledger_openings(holding_id,quantity,cost) VALUES (?,?,?)',(hid,r['quantity'],cost))
    def find_request(self, key):
        r=self.connection.execute('SELECT * FROM ledger_entries WHERE request_id=?',(key,)).fetchone()
        return dict(r) if r else None
    def get(self, eid):
        r=self.connection.execute('SELECT * FROM ledger_entries WHERE id=?',(eid,)).fetchone()
        if r is None:raise EntityNotFound(eid)
        return dict(r)
    def insert(self, entry):
        d=asdict(entry)
        return self.connection.execute('INSERT INTO ledger_entries('+','.join(d)+') VALUES ('+','.join('?' for _ in d)+')',tuple(d.values())).lastrowid
    def void(self,eid,day):self.connection.execute('UPDATE ledger_entries SET voided=1,voided_at=? WHERE id=?',(day,eid))
    @arithmetic
    def update_balances(self, balances, realized):
        for hid,b in balances.items():
            avg=b['cost']/b['quantity'] if b['quantity'] else 0
            self.connection.execute('UPDATE holdings SET quantity=?,avg_price=? WHERE id=?',(b['quantity'],avg,hid))
        for eid,result in realized.items():self.connection.execute('UPDATE ledger_entries SET realized=? WHERE id=?',(result,eid))
    def link_flow(self,eid,fid):self.connection.execute('INSERT INTO ledger_flows VALUES (?,?)',(eid,fid))
    def remove_flows(self,eid):
        self.connection.execute('DELETE FROM cashflows WHERE id IN (SELECT flow_id FROM ledger_flows WHERE entry_id=?)',(eid,))
    def invalidate_snapshots(self,eid,today):
        e=self.get(eid)
        for key in ('holding_id','cash_id','target_id'):
            if e[key]:
                self.connection.execute('INSERT OR IGNORE INTO ledger_invalid_snapshots SELECT account_id,date FROM snapshots WHERE account_id=(SELECT account_id FROM holdings WHERE id=?) AND date>=? AND date<?',(e[key],e['date'],today))
    def owned_flow(self,fid):return self.connection.execute('SELECT 1 FROM ledger_flows WHERE flow_id=?',(fid,)).fetchone() is not None
