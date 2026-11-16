"""Research links share the vault transaction with portfolio and position data."""
from wealth_manager.shared.records import RecordError
from .operations import get_row
from .research import RecordRepository
from .planning import SqlitePlanningReader

class CryptoRepository(RecordRepository):table='crypto_assets'

class Repository:
    def __init__(self,connection):self.connection=connection
    def target(self,kind,rid):
        if kind not in ('company','crypto'):raise RecordError('Тип позиции не соответствует выбранной записи')
        return get_row(self.connection,'companies' if kind=='company' else 'crypto_assets',rid)
    def save_crypto(self,record):
        if self.connection.execute('SELECT 1 FROM crypto_assets WHERE symbol=? COLLATE NOCASE AND network=? COLLATE NOCASE AND contract=? AND id<>?',(record.symbol,record.network,record.contract,record.id or -1)).fetchone():
            raise RecordError('Криптоактив с таким символом, сетью и контрактом уже существует')
        return CryptoRepository(self.connection).save(record)
    def delete_crypto(self,rid):
        self.target('crypto',rid)
        if self.connection.execute('SELECT 1 FROM holding_links WHERE crypto_id=?',(rid,)).fetchone():raise RecordError('Сначала удалите связи с позициями или отправьте запись в архив')
        CryptoRepository(self.connection).delete(rid)
    def holding_link(self,hid):
        row=self.connection.execute('SELECT * FROM holding_links WHERE holding_id=?',(hid,)).fetchone()
        return dict(row) if row else None
    def link(self,hid,kind,rid):
        self.connection.execute('INSERT INTO holding_links(holding_id,company_id,crypto_id) VALUES (?,?,?) ON CONFLICT(holding_id) DO UPDATE SET company_id=excluded.company_id,crypto_id=excluded.crypto_id',(hid,rid if kind=='company' else None,rid if kind=='crypto' else None))
    def unlink(self,hid):self.connection.execute('DELETE FROM holding_links WHERE holding_id=?',(hid,))

class Reader:
    def __init__(self,vault):self.vault=vault
    def read(self):
        with self.vault.lock_:
            data=SqlitePlanningReader(self.vault).read()
            for key,table in (('companies','companies'),('cryptos','crypto_assets'),('links','holding_links')):
                data[key]=[dict(r) for r in self.vault.con.execute('SELECT * FROM '+table)]
            return data
