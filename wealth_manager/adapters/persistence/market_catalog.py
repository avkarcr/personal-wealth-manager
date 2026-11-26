"""SQLite catalog adapter; issuers remain the original companies table."""
import sqlite3
from .research import RecordRepository
from .operations import get_row
from wealth_manager.shared.records import RecordError

TABLES={'sectors':'sectors','memberships':'company_sectors','companies':'companies','securities':'securities','listings':'security_listings','events':'listing_status_events','links':'holding_links'}


def snapshot(connection):
    return {key:[dict(r) for r in connection.execute('SELECT * FROM '+table)] for key,table in TABLES.items()}


class SectorRepository(RecordRepository):table='sectors'
class SecurityRepository(RecordRepository):table='securities'
class ListingRepository(RecordRepository):table='security_listings'


class Repository:
    def __init__(self,connection):self.connection=connection
    def read(self):return snapshot(self.connection)
    def save_sector(self,record):
        try:return SectorRepository(self.connection).save(record)
        except sqlite3.IntegrityError as error:raise RecordError('Отрасль с таким кодом уже существует') from error
    def delete_sector(self,rid):
        try: SectorRepository(self.connection).delete(rid)
        except sqlite3.IntegrityError as error: raise RecordError('У отрасли сохранены показатели; используйте архив') from error
    def assign_sector(self,cid,sid,primary,source,as_of):
        if primary:self.connection.execute('UPDATE company_sectors SET is_primary=0 WHERE company_id=?',(cid,))
        self.connection.execute('INSERT INTO company_sectors(company_id,sector_id,is_primary,source,as_of) VALUES (?,?,?,?,?) ON CONFLICT(company_id,sector_id) DO UPDATE SET is_primary=excluded.is_primary,source=excluded.source,as_of=excluded.as_of',(cid,sid,bool(primary),source,as_of))
    def remove_sector(self,cid,sid):self.connection.execute('DELETE FROM company_sectors WHERE company_id=? AND sector_id=?',(cid,sid))
    def save_security(self,record):
        try:return SecurityRepository(self.connection).save(record)
        except sqlite3.IntegrityError as error:raise RecordError('Выпуск с таким ISIN или регистрационным номером уже существует') from error
    def delete_security(self,rid):SecurityRepository(self.connection).delete(rid)
    def save_listing(self,record):
        old=get_row(self.connection,'security_listings',record.id) if record.id is not None else None
        rid=ListingRepository(self.connection).save(record)
        if not old or any(old[k]!=getattr(record,k) for k in ('status','as_of','source')):
            self.connection.execute('INSERT INTO listing_status_events(listing_id,status,as_of,source) VALUES (?,?,?,?)',(rid,record.status,record.as_of,record.source))
        return rid
    def delete_listing(self,rid):ListingRepository(self.connection).delete(rid)

    def legacy_listing(self,cid,name,ticker,exchange):
        from wealth_manager.modules.market_catalog.domain import Security,Listing
        sid=self.save_security(Security(cid,name,source='Legacy company card'))
        self.save_listing(Listing(sid,exchange,ticker,source='Legacy company card'))

    def delete_company_catalog(self,cid):
        if self.connection.execute('SELECT 1 FROM securities receipt JOIN securities underlying ON underlying.id=receipt.underlying_id WHERE underlying.company_id=? AND receipt.company_id<>?',(cid,cid)).fetchone():
            raise RecordError('Акции компании связаны с расписками другого эмитента; используйте архив')
        ids=self.connection.execute('SELECT id FROM securities WHERE company_id=? ORDER BY underlying_id IS NULL',(cid,)).fetchall()
        for row in ids:self.delete_security(row[0])


class Reader:
    def __init__(self,vault):self.vault=vault
    def read(self):
        with self.vault.lock_:
            if not self.vault.unlocked:raise RuntimeError('Vault is locked')
            return snapshot(self.vault.con)
