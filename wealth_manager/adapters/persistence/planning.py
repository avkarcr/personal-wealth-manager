"""Planning storage. The unit of work owns transactions and encrypted persistence."""
from dataclasses import asdict
from decimal import Decimal
from wealth_manager.modules.valuation.quality import holding_value
from wealth_manager.shared.classifications import asset_key
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.modules.planning.domain import PlanningError
from .operations import get_row


def sources(connection):
    result = []
    for row in connection.execute('SELECT h.*,e.closed_at,e.name AS owner FROM holdings h JOIN financial_accounts f ON f.id=h.account_id JOIN entities e ON e.id=f.id'):
        result.append(dict(kind='holding', id=row['id'], name=row['name'], owner=row['owner'],
                           currency=row['currency'], active=not row['closed_at'],
                           quantity=row['quantity'],updated_at=row['updated_at'],needs_quote=asset_key(row)!='cash',
                           value=None if holding_value(row) is None else Decimal(str(row['quantity'])) * Decimal(str(1 if asset_key(row)=='cash' else row['price']))))
    for row in connection.execute('SELECT a.id,a.current_value,a.valued_at,e.name,e.currency,e.closed_at FROM assets a JOIN entities e ON e.id=a.id'):
        result.append(dict(kind='asset', id=row['id'], name=row['name'], owner='',
                           currency=row['currency'], active=not row['closed_at'],needs_quote=True,updated_at=row['valued_at'], value=Decimal(str(row['current_value']))))
    return result


class SqlitePlanningRepository:
    def __init__(self, connection): self.connection = connection

    def goal(self, record_id): return get_row(self.connection, 'goals', record_id)
    def portfolio(self, record_id): return get_row(self.connection, 'portfolios', record_id)

    def _save(self, table, record):
        values = asdict(record)
        record_id = values.pop('id')
        values = {k: str(v) if isinstance(v, Decimal) else v for k, v in values.items()}
        if record_id is not None:
            get_row(self.connection, table, record_id)
            self.connection.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in values)+' WHERE id=?', tuple(values.values())+(record_id,))
            return record_id
        return self.connection.execute('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')', tuple(values.values())).lastrowid

    def save_goal(self, goal): return self._save('goals', goal)
    def save_portfolio(self, portfolio): return self._save('portfolios', portfolio)

    def delete_goal(self, record_id):
        self.goal(record_id)
        if self.connection.execute('SELECT 1 FROM portfolios WHERE goal_id=?', (record_id,)).fetchone():
            raise PlanningError('Сначала переназначьте или удалите портфели этой цели')
        self.connection.execute('DELETE FROM goals WHERE id=?', (record_id,))

    def delete_portfolio(self, record_id):
        self.portfolio(record_id)
        self.connection.execute('DELETE FROM portfolios WHERE id=?', (record_id,))

    def source(self, kind, record_id):
        source = next((s for s in sources(self.connection) if s['kind']==kind and s['id']==record_id), None)
        if source is None: raise EntityNotFound(record_id)
        return source

    @staticmethod
    def _column(kind):
        if kind not in ('holding', 'asset'): raise PlanningError('Неизвестный тип актива')
        return kind+'_id'

    def assigned(self, kind, record_id, excluding_portfolio):
        return self.connection.execute('SELECT COALESCE(SUM(share_bps),0) FROM allocations WHERE '+self._column(kind)+'=? AND portfolio_id<>?', (record_id, excluding_portfolio)).fetchone()[0]

    def allocate(self, portfolio_id, kind, record_id, share_bps):
        column = self._column(kind)
        row = self.connection.execute('SELECT id FROM allocations WHERE portfolio_id=? AND '+column+'=?', (portfolio_id, record_id)).fetchone()
        if row:
            self.connection.execute('UPDATE allocations SET share_bps=? WHERE id=?', (share_bps, row[0]))
        else:
            self.connection.execute('INSERT INTO allocations(portfolio_id,'+column+',share_bps) VALUES (?,?,?)', (portfolio_id, record_id, share_bps))

    def remove_allocation(self, portfolio_id, record_id):
        row = get_row(self.connection, 'allocations', record_id)
        if row['portfolio_id'] != portfolio_id: raise EntityNotFound(record_id)
        self.connection.execute('DELETE FROM allocations WHERE id=?', (record_id,))


class SqlitePlanningReader:
    def __init__(self, vault): self.vault = vault
    def read(self):
        with self.vault.lock_:
            if not self.vault.unlocked: raise RuntimeError('Vault is locked')
            con = self.vault.con
            return dict(goals=[dict(r) for r in con.execute('SELECT * FROM goals ORDER BY id')],
                        portfolios=[dict(r) for r in con.execute('SELECT * FROM portfolios ORDER BY id')],
                        allocations=[dict(r) for r in con.execute('SELECT * FROM allocations ORDER BY id')],
                        sources=sources(con), rate_dates=dict(con.execute('SELECT currency,updated_at FROM rates')), rates=dict(con.execute('SELECT currency,rate FROM rates')))
