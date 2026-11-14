"""Deterministic long-only ledger. No HTTP, clock, SQL or market-data dependencies."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from wealth_manager.shared.decimal import decimal, arithmetic, input_decimal
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.validation import valid_date

KINDS = {'buy':'Покупка','sell':'Продажа','deposit':'Пополнение','withdrawal':'Вывод',
         'income':'Доход','expense':'Расход / комиссия','transfer':'Перевод'}

@dataclass(frozen=True)
class Entry:
    date: str
    kind: str
    holding_id: int
    quantity: Decimal
    price: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    cash_id: Optional[int] = None
    target_id: Optional[int] = None
    note: str = ''
    request_id: str = ''

    def __post_init__(self):
        for key in ('quantity','price','fee'):object.__setattr__(self,key,decimal(getattr(self,key)))

    def validate(self):
        valid_date(self.date)
        if self.kind not in KINDS or self.holding_id<=0: raise RecordError('Некорректная операция журнала')
        for v in (self.quantity,self.price,self.fee): input_decimal(v)
        if self.quantity<=0 or self.price<0 or self.fee<0:
            raise RecordError('Количество должно быть положительным, цена и комиссия — неотрицательными')
        if not self.request_id or len(self.request_id)>100 or len(self.note)>2000:
            raise RecordError('Некорректный идентификатор операции')


def is_cash(holding): return holding['asset_class_code']=='cash'


def validate_links(entry, holdings, accounts, today):
    entry.validate()
    if entry.date>today: raise RecordError('Будущие операции нельзя проводить по журналу')
    ids=[entry.holding_id]+([entry.cash_id] if entry.cash_id else [])+([entry.target_id] if entry.target_id else [])
    for hid in ids:
        if hid not in holdings: raise RecordError('Позиция не найдена')
        account=accounts.get(holdings[hid]['account_id'])
        if not account or not account.get('ledger_started_on'):raise RecordError('Сначала включите журнал для обоих счетов операции')
        if account.get('closed_at'):raise RecordError('Сначала откройте закрытый счёт')
        if entry.date<account['ledger_started_on']:raise RecordError('Дата операции раньше входящих остатков')
    h=holdings[entry.holding_id]
    cash=holdings.get(entry.cash_id)
    if cash and (not is_cash(cash) or cash['account_id']!=h['account_id'] or cash['currency']!=h['currency']):
        raise RecordError('Денежная позиция должна принадлежать счёту и совпадать с валютой цены')
    if entry.kind in ('buy','sell'):
        if is_cash(h) or entry.price<=0 or cash is None or entry.target_id:
            raise RecordError('Для сделки укажите актив, цену и денежную позицию в валюте цены')
    elif entry.kind=='transfer':
        target=holdings.get(entry.target_id)
        if not target or target['account_id']==h['account_id'] or target['currency']!=h['currency'] or target['asset_class_code']!=h['asset_class_code']:
            raise RecordError('Перевод требует одинакового актива на разных счетах')
        if not is_cash(h) and (not h['ticker'] or h['ticker']!=target['ticker'] or entry.price<=0):
            raise RecordError('Для перевода актива нужны одинаковые тикеры и цена оценки')
        if entry.fee and cash is None:raise RecordError('Укажите денежную позицию для комиссии')
    else:
        if not is_cash(h) or entry.cash_id or entry.target_id or entry.fee:
            raise RecordError('Денежная операция требует только денежную позицию и сумму')


@arithmetic
def replay(openings, entries, holdings):
    """Replay by (date,id), including cost carried across transfers; reject negative balances."""
    balances={hid:dict(quantity=decimal(row['quantity']),cost=decimal(row['cost'])) for hid,row in openings.items()}
    realized={}
    def add(hid,quantity,cost):
        b=balances[hid];b['quantity']+=quantity;b['cost']+=cost
        if b['quantity']<0:raise RecordError('Недостаточно остатка: операция или отмена нарушает журнал')
        if not b['quantity']:b['cost']=Decimal(0)
    def remove(hid,quantity):
        b=balances[hid]
        if quantity>b['quantity']:raise RecordError('Недостаточно остатка: операция или отмена нарушает журнал')
        cost=b['cost'] if quantity==b['quantity'] else b['cost']*quantity/b['quantity']
        add(hid,-quantity,-cost)
        return cost
    for e in sorted(entries,key=lambda e:(e['date'],e['id'])):
        if e['voided']:continue
        hid=e['holding_id'];q=decimal(e['quantity']);p=decimal(e['price']);fee=decimal(e['fee']);kind=e['kind']
        realized[e['id']]=None
        if kind=='buy':
            remove(e['cash_id'],q*p+fee);add(hid,q,q*p+fee)
        elif kind=='sell':
            if fee>q*p:raise RecordError('Комиссия превышает сумму продажи')
            cost=remove(hid,q);add(e['cash_id'],q*p-fee,q*p-fee);realized[e['id']]=q*p-fee-cost
        elif kind=='transfer':
            cost=remove(hid,q);add(e['target_id'],q,cost)
            if fee:remove(e['cash_id'],fee)
        elif kind in ('deposit','income'):add(hid,q,q)
        else:remove(hid,q)
    return balances,realized
