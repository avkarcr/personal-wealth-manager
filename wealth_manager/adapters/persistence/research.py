"""SQLite adapters for independent company, macro and tax modules."""
from dataclasses import asdict
from decimal import Decimal
from wealth_manager.shared.records import RecordError
from .operations import get_row


class RecordRepository:
    table = None
    def __init__(self,connection): self.connection=connection
    def save(self,record):
        data=asdict(record);record_id=data.pop('id')
        values=tuple(str(v) if isinstance(v,Decimal) else v for v in data.values())
        if record_id is not None:
            get_row(self.connection,self.table,record_id)
            self.connection.execute('UPDATE '+self.table+' SET '+','.join(k+'=?' for k in data)+' WHERE id=?',values+(record_id,))
            return record_id
        return self.connection.execute('INSERT INTO '+self.table+' ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',values).lastrowid
    def delete(self,record_id):
        get_row(self.connection,self.table,record_id)
        self.connection.execute('DELETE FROM '+self.table+' WHERE id=?',(record_id,))


class CompanyRepository(RecordRepository):
    table='companies'
    def watch(self, record_id):
        if get_row(self.connection, self.table, record_id)['status'] == 'watch':
            return False
        self.connection.execute("UPDATE companies SET status='watch' WHERE id=?", (record_id,))
        return True

    def delete(self,record_id):
        get_row(self.connection,self.table,record_id)
        if self.connection.execute('SELECT 1 FROM holding_links WHERE company_id=?',(record_id,)).fetchone():
            raise RecordError('Сначала удалите связи с позициями или отправьте запись в архив')
        from .market_catalog import Repository
        Repository(self.connection).delete_company_catalog(record_id)
        super().delete(record_id)
    def save(self,record):
        old=get_row(self.connection,self.table,record.id) if record.id is not None else None
        if old and (old['ticker'],old['exchange'])!=(record.ticker,record.exchange):
            raise RecordError('Изменяйте тикеры в листингах выпуска; исходные поля компании сохранены для совместимости')
        if not old and record.ticker and self.connection.execute('SELECT 1 FROM security_listings WHERE ticker=? COLLATE NOCASE AND exchange=? COLLATE NOCASE',(record.ticker,record.exchange)).fetchone():
            raise RecordError('Компания с таким тикером и биржей уже существует')
        rid=super().save(record)
        if not old and record.ticker:
            from .market_catalog import Repository
            Repository(self.connection).legacy_listing(rid,record.name,record.ticker,record.exchange)
        return rid


from .macro_data import Repository as ObservationRepository


class EntryRepository(RecordRepository): table='tax_entries'
class DeductionRepository(RecordRepository): table='tax_deductions'

class TaxRepository:
    def __init__(self,connection):
        self.entries=EntryRepository(connection);self.deductions=DeductionRepository(connection)
    def save_entry(self,record): return self.entries.save(record)
    def save_deduction(self,record): return self.deductions.save(record)
    def delete_entry(self,record_id): self.entries.delete(record_id)
    def delete_deduction(self,record_id): self.deductions.delete(record_id)


class TableReader:
    table=None
    decimal_fields=()
    def __init__(self,vault): self.vault=vault
    def read(self):
        with self.vault.lock_:
            if not self.vault.unlocked: raise RuntimeError('Vault is locked')
            rows=[dict(r) for r in self.vault.con.execute('SELECT * FROM '+self.table+' ORDER BY id DESC')]
            for row in rows:
                for key in self.decimal_fields: row[key]=Decimal(row[key])
            return rows

class CompanyReader:
    def __init__(self, vault):
        self.vault = vault

    def read(self):
        from .market_catalog import research_snapshot
        with self.vault.lock_:
            if not self.vault.unlocked:
                raise RuntimeError('Vault is locked')
            connection = self.vault.con
            data = research_snapshot(connection)
            data['portfolio_links'] = [dict(row) for row in connection.execute("""
                SELECT link.company_id, p.id, p.name, h.quantity
                FROM holding_links link
                JOIN holdings h ON h.id = link.holding_id
                JOIN entities e ON e.id = h.account_id
                JOIN allocations a ON a.holding_id = h.id
                JOIN portfolios p ON p.id = a.portfolio_id
                WHERE link.company_id IS NOT NULL AND COALESCE(e.closed_at,'') = '' AND a.share_bps > 0
            """)]
            return data
class ObservationReader(TableReader):
    table='macro_observations';decimal_fields=('value',)
class EntryReader(TableReader):
    table='tax_entries';decimal_fields=('base','rate','paid')
class DeductionReader(TableReader):
    table='tax_deductions';decimal_fields=('amount','refund','actual_refund')

class TaxReader:
    def __init__(self,vault): self.vault=vault
    def read(self):
        with self.vault.lock_:
            from .tax_evidence import DOCUMENT_FIELDS
            return dict(entries=EntryReader(self.vault).read(),deductions=DeductionReader(self.vault).read(),
                        payments=[dict(r) for r in self.vault.con.execute('SELECT * FROM tax_payments ORDER BY date DESC,id DESC')],
                        documents=[dict(r) for r in self.vault.con.execute('SELECT '+DOCUMENT_FIELDS+' FROM tax_documents ORDER BY id DESC')])
