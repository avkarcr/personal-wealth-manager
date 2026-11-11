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
    def save(self,record):
        if record.ticker and self.connection.execute('SELECT 1 FROM companies WHERE ticker=? COLLATE NOCASE AND exchange=? COLLATE NOCASE AND id<>?',(record.ticker,record.exchange,record.id or -1)).fetchone():
            raise RecordError('Компания с таким тикером и биржей уже существует')
        return super().save(record)


class ObservationRepository(RecordRepository):
    table='macro_observations'
    def save(self,record):
        if self.connection.execute('SELECT 1 FROM macro_observations WHERE country=? AND indicator=? AND date=? AND id<>?',(record.country,record.indicator,record.date,record.id or -1)).fetchone():
            raise RecordError('Показатель за эту дату уже есть: отредактируйте запись')
        return super().save(record)


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

class CompanyReader(TableReader): table='companies'
class ObservationReader(TableReader):
    table='macro_observations';decimal_fields=('value',)
class EntryReader(TableReader):
    table='tax_entries';decimal_fields=('base','rate','paid')
class DeductionReader(TableReader):
    table='tax_deductions';decimal_fields=('amount','refund')

class TaxReader:
    def __init__(self,vault): self.vault=vault
    def read(self):
        with self.vault.lock_:
            return dict(entries=EntryReader(self.vault).read(),deductions=DeductionReader(self.vault).read())
