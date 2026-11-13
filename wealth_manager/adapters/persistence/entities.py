"""Typed entity storage with a shared identity and read-only legacy projection."""
from datetime import date
from wealth_manager.modules.accounts.domain import AccountNotFound
from wealth_manager.modules.planning.domain import PlanningError

class SqliteEntityRepository:
    def __init__(self,connection): self.connection=connection
    def get(self,record_id):
        row=self.connection.execute('SELECT * FROM accounts WHERE id=?',(record_id,)).fetchone()
        if row is None:raise AccountNotFound(record_id)
        return dict(row)
    def toggle_archived(self,record_id):
        self.get(record_id)
        self.connection.execute('UPDATE entities SET archived=1-archived WHERE id=?',(record_id,))
    def can_close(self,record_id,day):
        return not self.connection.execute('SELECT 1 FROM holdings WHERE account_id=? AND quantity<>0',(record_id,)).fetchone() and not self.connection.execute('SELECT 1 FROM cashflows WHERE account_id=? AND (pending=1 OR date>?)',(record_id,day)).fetchone()
    def set_closed(self,record_id,day,event_day=None):
        self.get(record_id)
        self.connection.execute('UPDATE entities SET closed_at=? WHERE id=?',(day,record_id))
        self.connection.execute('INSERT INTO account_status_events(account_id,date,closed) VALUES (?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET closed=excluded.closed',(record_id,event_day or day,int(bool(day))))
    def delete(self,record_id):
        self.get(record_id)
        if self.connection.execute('SELECT 1 FROM allocations WHERE asset_id=? OR holding_id IN (SELECT id FROM holdings WHERE account_id=?)', (record_id,record_id)).fetchone():
            raise PlanningError('Сначала удалите распределение актива из портфелей')
        self.connection.execute('DELETE FROM entities WHERE id=?',(record_id,))
        self.connection.execute('DELETE FROM legacy_accounts_v3 WHERE id=?',(record_id,))

class TypedRepository(SqliteEntityRepository):
    table = None
    entity_type = None
    def get(self,record_id):
        record=super().get(record_id)
        if not self.connection.execute('SELECT 1 FROM '+self.table+' WHERE id=?',(record_id,)).fetchone():
            raise ValueError('Entity type cannot be changed')
        return record
    def save_common(self,entity):
        values=(entity.name,entity.currency,entity.note)
        if entity.id is not None:
            self.get(entity.id)
            self.connection.execute('UPDATE entities SET name=?,currency=?,note=? WHERE id=?',values+(entity.id,))
            return entity.id
        return self.connection.execute('INSERT INTO entities(name,currency,note,entity_type) VALUES (?,?,?,?)',values+(self.entity_type,)).lastrowid

class SqliteFinancialAccountRepository(TypedRepository):
    table='financial_accounts';entity_type='account'
    def save(self,account):
        record_id=self.save_common(account)
        self.connection.execute('INSERT INTO financial_accounts(id,kind,institution) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,institution=excluded.institution',(record_id,account.kind,account.institution))
        return record_id

class SqliteAssetRepository(TypedRepository):
    table='assets';entity_type='asset'
    def save(self,asset):
        old=self.get(asset.id) if asset.id is not None else None
        record_id=self.save_common(asset)
        self.connection.execute('INSERT INTO assets(id,current_value,institution) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET current_value=excluded.current_value,institution=excluded.institution',(record_id,float(asset.current_value),asset.institution))
        if old is None or old['current_value']!=float(asset.current_value) or old['currency']!=asset.currency:
            self.connection.execute('UPDATE assets SET valued_at=? WHERE id=?',(date.today().isoformat(),record_id))
        return record_id
    def set_value(self,record_id,value):
        self.get(record_id)
        self.connection.execute('UPDATE assets SET current_value=?,valued_at=? WHERE id=?',(value,date.today().isoformat(),record_id))
    def record_purchase(self,record_id,currency,purchase):
        self.connection.execute('INSERT INTO cashflows(account_id,date,type,amount,currency,note) VALUES (?,?,?,?,?,?)',(record_id,purchase.date.isoformat(),'deposit',float(purchase.amount),currency,'Покупка объекта'))

class SqliteIncomeSourceRepository(TypedRepository):
    table='income_sources';entity_type='income_source'
    def save(self,source):
        record_id=self.save_common(source)
        self.connection.execute('INSERT INTO income_sources(id,tax_rate,institution) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET tax_rate=excluded.tax_rate,institution=excluded.institution',(record_id,float(source.tax_rate),source.institution))
        return record_id
