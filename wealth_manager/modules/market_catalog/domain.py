"""Issuer classifications and stable issue identities, independent of storage."""
from dataclasses import dataclass
from typing import Optional
import re
from wealth_manager.shared.records import required, iso_date, currency_code, website, RecordError
from wealth_manager.shared.errors import EntityNotFound

SECURITY_TYPES = {'unknown':'Тип выпуска не уточнён','common_share':'Обыкновенная акция','preferred_share':'Привилегированная акция','bond':'Облигация','depositary_receipt':'Депозитарная расписка','other':'Другой выпуск'}
LISTING_STATUSES = {'unknown':'Статус торгов не подтверждён','active':'Торгуется','suspended':'Торги приостановлены','delisted':'Делистинг'}


def find(rows, rid):
    result=next((r for r in rows if r['id']==rid),None)
    if result is None:raise EntityNotFound(rid)
    return result


def identifier(value):
    if type(value)!=int or value<=0:raise RecordError('Некорректный идентификатор справочника')


def dated(value):
    if value:iso_date(value)


def security_class(kind):
    return 'stocks' if kind in ('common_share','preferred_share','depositary_receipt') else 'bonds' if kind=='bond' else None


def overlaps(a,b):
    return (a['valid_from'] or '0001-01-01') <= (b['valid_to'] or '9999-12-31') and (b['valid_from'] or '0001-01-01') <= (a['valid_to'] or '9999-12-31')


@dataclass(frozen=True)
class Sector:
    code: str
    name: str
    name_en: str = ''
    parent_id: Optional[int] = None
    source_url: str = ''
    archived: bool = False
    id: Optional[int] = None

    def validate(self):
        required(self.name);website(self.source_url)
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',self.code):raise RecordError('Код отрасли: латинские строчные буквы, цифры, дефис или подчёркивание')
        if self.parent_id is not None:identifier(self.parent_id)


@dataclass(frozen=True)
class Security:
    company_id: int
    name: str
    kind: str = 'unknown'
    isin: str = ''
    registration_number: str = ''
    currency: str = ''
    underlying_id: Optional[int] = None
    archived: bool = False
    source: str = ''
    as_of: str = ''
    id: Optional[int] = None

    def validate(self):
        identifier(self.company_id);required(self.name);dated(self.as_of)
        if self.kind not in SECURITY_TYPES:raise RecordError('Некорректный тип выпуска')
        if self.currency:currency_code(self.currency)
        if self.isin and not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]',self.isin):raise RecordError('ISIN должен содержать 12 символов в установленном формате')
        if self.underlying_id is not None:
            identifier(self.underlying_id)
            if self.kind!='depositary_receipt':raise RecordError('Базовый выпуск указывается только для депозитарной расписки')


@dataclass(frozen=True)
class Listing:
    security_id: int
    exchange: str
    ticker: str
    board: str = ''
    valid_from: str = ''
    valid_to: str = ''
    status: str = 'unknown'
    as_of: str = ''
    source: str = ''
    id: Optional[int] = None

    def validate(self):
        identifier(self.security_id);required(self.exchange);required(self.ticker)
        for d in (self.valid_from,self.valid_to,self.as_of):dated(d)
        if self.valid_from and self.valid_to and self.valid_to<self.valid_from:raise RecordError('Конец листинга не может быть раньше начала')
        if self.status not in LISTING_STATUSES:raise RecordError('Некорректный статус торгов')
        if self.status!='unknown' and (not self.as_of or not self.source.strip()):raise RecordError('Для статуса торгов укажите дату подтверждения и источник')
