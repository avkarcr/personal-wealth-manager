"""SQLite catalog adapter; issuers remain the original companies table."""
from datetime import date
from decimal import Decimal
import sqlite3
from .research import RecordRepository
from .operations import get_row
from wealth_manager.modules.market_catalog.domain import Listing, Security
from wealth_manager.modules.market_catalog.iss import Quote, Reference, Snapshot, Target
from wealth_manager.shared.records import RecordError

TABLES={'sectors':'sectors','memberships':'company_sectors','companies':'companies','securities':'securities','listings':'security_listings','events':'listing_status_events','links':'holding_links'}


def snapshot(connection):
    return {key:[dict(r) for r in connection.execute('SELECT * FROM '+table)] for key,table in TABLES.items()}


def iss_snapshot(row):
    quote = None
    if row['day'] is not None:
        quote = Quote(
            date.fromisoformat(row['day']), Decimal(row['close']), row['basis'],
            Decimal(row['change']) if row['change'] is not None else None,
            Decimal(row['turnover']) if row['turnover'] is not None else None,
            date.fromisoformat(row['previous_day']) if row['previous_day'] is not None else None,
        )
    return Snapshot(Reference(row['isin'], row['registration_number'], row['name']), quote,
                    row['checked_at'], row['access'])


def research_snapshot(connection):
    data = snapshot(connection)
    data['iss'] = {row['listing_id']: iss_snapshot(row) for row in
                   connection.execute('SELECT * FROM listing_iss_snapshots')}
    return data


class SectorRepository(RecordRepository):table='sectors'
class SecurityRepository(RecordRepository):table='securities'
class ListingRepository(RecordRepository):table='security_listings'


class Repository:
    def __init__(self,connection):self.connection=connection
    def read(self):return snapshot(self.connection)

    def iss_target(self, listing_id):
        listing = Listing(**get_row(self.connection, 'security_listings', listing_id))
        security = Security(**get_row(self.connection, 'securities', listing.security_id))
        company = get_row(self.connection, 'companies', security.company_id)
        row = self.connection.execute('SELECT * FROM listing_iss_snapshots WHERE listing_id=?', (listing_id,)).fetchone()
        return Target(listing, security, company['status'] == 'archived', iss_snapshot(row) if row else None)

    def save_iss(self, listing_id, snapshot):
        reference, quote = snapshot.reference, snapshot.quote
        values = (
            listing_id, reference.isin, reference.registration_number, reference.name, snapshot.checked_at, snapshot.access,
            quote.day.isoformat() if quote else None,
            str(quote.close) if quote else None,
            quote.basis if quote else None,
            str(quote.change) if quote and quote.change is not None else None,
            str(quote.turnover) if quote and quote.turnover is not None else None,
            quote.previous_day.isoformat() if quote and quote.previous_day else None,
        )
        self.connection.execute("""
            INSERT INTO listing_iss_snapshots(listing_id,isin,registration_number,name,checked_at,access,day,close,basis,change,turnover,previous_day)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(listing_id) DO UPDATE SET
                isin=excluded.isin, registration_number=excluded.registration_number, name=excluded.name,
                checked_at=excluded.checked_at, access=excluded.access,
                day=excluded.day, close=excluded.close, basis=excluded.basis, change=excluded.change,
                turnover=excluded.turnover, previous_day=excluded.previous_day
        """, values)

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
