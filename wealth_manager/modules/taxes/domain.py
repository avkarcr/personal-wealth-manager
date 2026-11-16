from wealth_manager.shared.decimal import arithmetic
"""User-entered tax estimates; no statutory rates or deduction eligibility rules."""
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from wealth_manager.shared.records import country_code, currency_code, number, year_number, required, money, RecordError

STATUSES={'collecting':'Собираю документы','ready':'Готово к подаче','submitted':'Подано','received':'Получено','rejected':'Отказ'}

@dataclass(frozen=True)
class TaxEntry:
    year: int
    country: str
    category: str
    currency: str
    base: Decimal
    rate: Decimal
    paid: Decimal = Decimal('0')
    note: str = ''
    id: Optional[int] = None
    def validate(self):
        year_number(self.year); country_code(self.country); currency_code(self.currency); required(self.category)
        money(self.base); number(self.rate,0,100); money(self.paid)

@dataclass(frozen=True)
class Deduction:
    year: int
    country: str
    category: str
    currency: str
    amount: Decimal
    refund: Decimal = Decimal('0')
    status: str = 'collecting'
    documents: str = ''
    note: str = ''
    id: Optional[int] = None
    actual_refund: Optional[Decimal] = None
    received_date: str = ''

    def normalized(self):
        return replace(self,actual_refund=(self.refund if self.status=='received' else Decimal(0)) if self.actual_refund is None else self.actual_refund)

    def validate(self):
        from wealth_manager.shared.validation import valid_date
        valid_date(self.received_date,optional=True)
        money(self.normalized().actual_refund)
        year_number(self.year); country_code(self.country); currency_code(self.currency); required(self.category)
        money(self.amount); money(self.refund)
        if self.status not in STATUSES: raise RecordError('Некорректный статус')


@arithmetic
def estimate(entry):
    return (Decimal(entry['base'])*Decimal(entry['rate'])/100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


@arithmetic
def summarize(entries,deductions):
    groups={}
    def group(row):
        key=(row['year'],row['country'],row['currency'])
        return groups.setdefault(key,dict(year=key[0],country=key[1],currency=key[2],base=Decimal(0),tax=Decimal(0),paid=Decimal(0),refund_expected=Decimal(0),refund_received=Decimal(0)))
    for row in entries:
        item=group(row); item['base']+=Decimal(row['base']); item['tax']+=estimate(row); item['paid']+=Decimal(row['paid'])
    for row in deductions:
        item=group(row)
        actual=Decimal(row.get('actual_refund',row['refund'] if row['status']=='received' else 0))
        item['refund_received']+=actual
        if row['status'] not in ('received','rejected'):item['refund_expected']+=max(Decimal(row['refund'])-actual,Decimal(0))
    for item in groups.values(): item['balance']=item['tax']-item['paid']
    return sorted(groups.values(),key=lambda r:(-r['year'],r['country'],r['currency']))
