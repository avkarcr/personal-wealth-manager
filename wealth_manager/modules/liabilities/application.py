from dataclasses import asdict
from wealth_manager.shared.decimal import arithmetic
from wealth_manager.shared.records import RecordError
from wealth_manager.modules.ledger.domain import Entry
from .domain import outstanding

class Commands:
    def __init__(self,factory,ledger,today):self.factory,self.ledger,self.today=factory,ledger,today
    @staticmethod
    def repeated(old,record):
        if old and (old['voided'] or any(old[k]!=v for k,v in asdict(record).items())):
            raise RecordError('Идентификатор уже использован для другой или отменённой записи')
        return old
    def create(self,debt):
        debt.validate(self.today().isoformat())
        with self.factory() as uow:
            old=self.repeated(uow.liabilities.find_debt(debt.request_id),debt)
            if old:return old['id']
            if debt.asset_id is not None:uow.assets.get(debt.asset_id)
            lid=uow.liabilities.create(debt);uow.commit();return lid
    @arithmetic
    def pay(self,payment):
        with self.factory() as uow:
            debt=uow.liabilities.get(payment.liability_id);payment.validate(debt,self.today().isoformat())
            old=self.repeated(uow.liabilities.find_payment(payment.request_id),payment)
            if old:return old['id']
            if outstanding(debt,uow.liabilities.payments(),self.today().isoformat())<payment.principal:
                raise RecordError('Погашение превышает остаток основного долга')
            if payment.cash_id is not None:
                h=uow.ledger.data()['holdings'].get(payment.cash_id)
                if not h or h['asset_class_code']!='cash' or h['currency']!=debt['currency']:
                    raise RecordError('Выберите деньги в валюте обязательства на счёте с журналом')
            pid=uow.liabilities.pay(payment)
            if payment.cash_id is not None:
                for part,kind,amount in (('principal','withdrawal',payment.principal),('cost','expense',payment.interest+payment.fee)):
                    if amount:
                        eid=self.ledger.post_in_uow(uow,Entry(date=payment.date,kind=kind,holding_id=payment.cash_id,
                            quantity=amount,note=debt['name']+' · '+payment.note,request_id=f'liability:{pid}:{part}'))
                        uow.liabilities.link(pid,eid)
                uow.take_snapshot()
            uow.commit();return pid
    def cancel_payment(self,pid):
        with self.factory() as uow:
            p=uow.liabilities.payment(pid)
            if p['voided']:return
            for eid in uow.liabilities.entries(pid):self.ledger.cancel_in_uow(uow,eid)
            uow.liabilities.cancel_payment(pid);uow.take_snapshot();uow.commit()
    def cancel(self,lid):
        with self.factory() as uow:
            debt=uow.liabilities.get(lid)
            if debt['voided']:return
            if any(p['liability_id']==lid and not p['voided'] for p in uow.liabilities.payments()):
                raise RecordError('Сначала отмените платежи по обязательству')
            uow.liabilities.cancel_debt(lid);uow.commit()
