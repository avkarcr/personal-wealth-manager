"""Outstanding principal and debt service, independent of persistence and HTTP."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from wealth_manager.shared.decimal import input_decimal, arithmetic, decimal
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.validation import valid_date
from wealth_manager.modules.valuation.domain import complete_sum

KINDS={'loan':'Кредит / заём','mortgage':'Ипотека','deposit':'Возвратный залог арендатора'}

@dataclass(frozen=True)
class Liability:
    name: str
    opening_date: str
    opening_principal: Decimal
    currency: str='RUB'
    kind: str='loan'
    asset_id: Optional[int]=None
    creditor: str=''
    due_date: str=''
    note: str=''
    request_id: str=''

    def __post_init__(self):
        object.__setattr__(self,"opening_principal",decimal(self.opening_principal))
        object.__setattr__(self,"currency",self.currency.strip().upper())

    def validate(self,today):
        valid_date(self.opening_date);valid_date(self.due_date,optional=True)
        input_decimal(self.opening_principal)
        if not self.name.strip() or len(self.name)>200 or len(self.creditor)>200 or not self.currency or len(self.currency)>10 or self.kind not in KINDS or self.opening_principal<=0 or self.opening_date>today or (self.due_date and self.due_date<self.opening_date):
            raise RecordError('Проверьте название, валюту, положительный долг и даты обязательства')
        if not self.request_id or len(self.request_id)>100 or len(self.note)>2000:
            raise RecordError('Некорректный идентификатор операции')
        if self.kind=='deposit' and self.asset_id is None:raise RecordError('Для залога укажите объект недвижимости')

@dataclass(frozen=True)
class Payment:
    liability_id: int
    date: str
    principal: Decimal=Decimal(0)
    interest: Decimal=Decimal(0)
    fee: Decimal=Decimal(0)
    cash_id: Optional[int]=None
    note: str=''
    request_id: str=''

    def __post_init__(self):
        for field in ("principal","interest","fee"):object.__setattr__(self,field,decimal(getattr(self,field)))

    @arithmetic
    def validate(self,liability,today):
        valid_date(self.date)
        for value in (self.principal,self.interest,self.fee):input_decimal(value)
        if min(self.principal,self.interest,self.fee)<0 or self.principal+self.interest+self.fee<=0:
            raise RecordError('Платёж должен быть положительным, его части — неотрицательными')
        if not liability['opening_date']<=self.date<=today or liability['voided']:
            raise RecordError('Проверьте дату платежа и состояние обязательства')
        if not self.request_id or len(self.request_id)>100 or len(self.note)>2000:raise RecordError('Некорректный идентификатор операции')

@arithmetic
def outstanding(liability,payments,day):
    return liability['opening_principal']-sum(p['principal'] for p in payments if p['liability_id']==liability['id'] and not p['voided'] and p['date']<=day)

@arithmetic
def summary(data,rates,today,asset_id=None):
    day=today.isoformat();rows=[]
    for debt in data.get('liabilities',[]):
        if debt['voided'] or debt['opening_date']>day or (asset_id is not None and debt['asset_id']!=asset_id):continue
        amount=outstanding(debt,data.get('liability_payments',[]),day)
        rows.append(dict(debt,outstanding=amount,amount_base=rates.to_base(amount,debt['currency']),
                         overdue=bool(amount and debt['due_date'] and debt['due_date']<day)))
    return dict(rows=rows,total=complete_sum(r['amount_base'] for r in rows),
                missing=sorted({r['currency'] for r in rows if r['amount_base'] is None}))
