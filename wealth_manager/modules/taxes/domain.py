"""User-entered tax estimates; no statutory rates or deduction eligibility rules."""
from dataclasses import dataclass
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
    def validate(self):
        year_number(self.year); country_code(self.country); currency_code(self.currency); required(self.category)
        money(self.amount); money(self.refund)
        if self.status not in STATUSES: raise RecordError('Некорректный статус')


def estimate(entry):
    return (Decimal(entry['base'])*Decimal(entry['rate'])/100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


def summarize(entries,deductions):
    groups={}
    def group(row):
        key=(row['year'],row['country'],row['currency'])
        return groups.setdefault(key,dict(year=key[0],country=key[1],currency=key[2],base=Decimal(0),tax=Decimal(0),paid=Decimal(0),refund_expected=Decimal(0),refund_received=Decimal(0)))
    for row in entries:
        item=group(row); item['base']+=Decimal(row['base']); item['tax']+=estimate(row); item['paid']+=Decimal(row['paid'])
    for row in deductions:
        item=group(row)
        if row['status']=='received': item['refund_received']+=Decimal(row['refund'])
        elif row['status']!='rejected': item['refund_expected']+=Decimal(row['refund'])
    for item in groups.values(): item['balance']=item['tax']-item['paid']
    return sorted(groups.values(),key=lambda r:(-r['year'],r['country'],r['currency']))
