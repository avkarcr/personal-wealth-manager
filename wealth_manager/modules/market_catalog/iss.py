"""ISS reference and dated trading data for an explicitly identified listing."""
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Optional

from wealth_manager.shared.decimal import arithmetic
from wealth_manager.shared.records import RecordError
from .domain import Listing, Security


BOARD = 'TQBR'
MOEX_TIME = timezone(timedelta(hours=3))
ACCESS_MODES = ('familiarization', 'agreement')


@dataclass(frozen=True)
class Reference:
    isin: str
    registration_number: str
    name: str

    def matches(self, security):
        pairs = [
            (security['isin'], self.isin),
            (security['registration_number'], self.registration_number),
        ]
        comparisons = [local.casefold() == remote.casefold() for local, remote in pairs if local and remote]
        return bool(comparisons) and all(comparisons)


@dataclass(frozen=True)
class Quote:
    day: date
    close: Decimal
    basis: str = 'previous'
    change: Optional[Decimal] = None
    turnover: Optional[Decimal] = None
    previous_day: Optional[date] = None

    def within(self, start, end):
        if not in_period(self.day.isoformat(), start, end):
            return None
        if self.previous_day and not in_period(self.previous_day.isoformat(), start, end):
            return replace(self, change=None, previous_day=None)
        return self


@dataclass(frozen=True)
class Snapshot:
    reference: Reference
    quote: Optional[Quote]
    checked_at: str
    access: str


@dataclass(frozen=True)
class Target:
    listing: Listing
    security: Security
    company_archived: bool
    saved: Optional[Snapshot]


def supported(security_kind, exchange, board, ticker):
    return (
        security_kind in ('common_share', 'preferred_share', 'depositary_receipt')
        and exchange.upper() == 'MOEX'
        and board.upper() == BOARD
        and re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,35}', ticker.upper()) is not None
    )


def in_period(day, start, end):
    return (not start or start <= day) and (not end or day <= end)


class ListingSync:
    def __init__(self, provider, session, clock=lambda: datetime.now(timezone.utc)):
        self.provider = provider
        self.session = session
        self.clock = clock

    @arithmetic
    def execute(self, listing_id, token):
        target, access = self.session.iss_target(token, listing_id)
        now = self.clock()
        today = now.astimezone(MOEX_TIME).date()
        listing, security = target.listing, target.security
        if access not in ACCESS_MODES:
            raise RecordError('Выберите режим доступа MOEX')
        if (
            not supported(security.kind, listing.exchange, listing.board, listing.ticker)
            or target.company_archived or security.archived
            or listing.status in ('suspended', 'delisted')
            or not in_period(today.isoformat(), listing.valid_from, listing.valid_to)
        ):
            raise RecordError('Листинг недоступен для обновления ISS')
        if not security.isin and not security.registration_number:
            raise RecordError('Для ISS укажите ISIN или регистрационный номер выпуска')

        ticker = listing.ticker.upper()
        reference, quote = self.provider.reference(ticker)
        identity = dict(isin=security.isin, registration_number=security.registration_number)
        if not reference.matches(identity):
            raise RecordError('Идентификаторы выпуска в каталоге и ISS не совпадают')

        end = today - timedelta(days=1)
        if quote:
            quote = quote.within(listing.valid_from, end.isoformat())
        if listing.valid_from:
            start = max(date.fromisoformat(listing.valid_from), today - timedelta(days=30))
            if start <= end:
                bars = self.provider.candles(ticker, start, end)
                if bars and (quote is None or bars[-1].day >= quote.day):
                    quote = bars[-1]
                    if len(bars) > 1:
                        change = quote.close / bars[-2].close - 1
                        quote = replace(quote, change=change, previous_day=bars[-2].day)

        saved = target.saved
        if saved and saved.quote and saved.reference.matches(identity) and saved.reference.matches(asdict(reference)):
            old = saved.quote.within(listing.valid_from, end.isoformat())
            if old and (quote is None or (old.day, old.basis == 'candle') > (quote.day, quote.basis == 'candle')):
                quote = old

        snapshot = Snapshot(reference, quote, now.astimezone(timezone.utc).isoformat(timespec='seconds'), access)
        with self.session.write(token) as uow:
            if uow.macro_data.refresh_state()['access'] != access or uow.market_catalog.iss_target(listing_id) != target:
                raise RecordError('Листинг или режим доступа изменился. Повторите обновление ISS')
            if snapshot != saved:
                uow.market_catalog.save_iss(listing_id, snapshot)
                uow.commit()
        return snapshot
