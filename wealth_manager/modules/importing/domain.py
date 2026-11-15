"""Import identity and exact, dated reconciliation; no file or database I/O."""
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from wealth_manager.modules.ledger.domain import Entry, replay
from wealth_manager.shared.decimal import arithmetic, input_decimal
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.validation import valid_date


class ImportRowError(RecordError):
    def __init__(self,line,message):
        super().__init__(message)
        self.line=line


@dataclass(frozen=True)
class ImportRow:
    line: int
    external_id: str
    currency: str
    entry: Entry


def identify(row, source, account_id):
    if not source.strip() or len(source)>80 or not row.external_id or len(row.external_id)>100:
        raise RecordError('Укажите источник и уникальный внешний ID каждой операции')
    identity=json.dumps([account_id,source.strip(),row.external_id],ensure_ascii=False)
    return replace(row.entry,request_id='import:'+sha256(identity.encode()).hexdigest(),
                   note=f'[{source.strip()} / {row.external_id}] {row.entry.note}')


def revision(data):
    return sha256(json.dumps(data,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


@arithmetic
def reconcile(data, account_id, day, statement, today):
    valid_date(day)
    account=data['accounts'].get(account_id)
    if not account or not account['ledger_started_on'] or not account['ledger_started_on']<=day<=today:
        raise RecordError('Для сверки выберите счёт с журналом и дату от входящих остатков до сегодня')
    holdings={hid:h for hid,h in data['holdings'].items() if h['account_id']==account_id}
    supplied={}
    for hid,currency,quantity in statement:
        quantity=input_decimal(quantity)
        if hid not in holdings or holdings[hid]['currency']!=currency or hid in supplied or quantity<0:
            raise RecordError('Проверьте счёт, валюту, уникальность позиций и неотрицательные остатки')
        supplied[hid]=quantity
    if not supplied:raise RecordError('Файл не содержит данных')
    balances,_=replay(data['openings'],[e for e in data['entries'] if e['date']<=day],data['holdings'])
    result=[]
    for hid,h in holdings.items():
        actual=balances[hid]['quantity'];expected=supplied.get(hid)
        result.append(dict(holding_id=hid,name=h['name'],currency=h['currency'],actual=actual,expected=expected,
                           difference=None if expected is None else actual-expected,
                           status='missing' if expected is None else 'match' if actual==expected else 'difference'))
    return result
