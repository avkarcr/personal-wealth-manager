from dataclasses import asdict
from decimal import Decimal
from wealth_manager.shared.decimal import decimal, arithmetic
from wealth_manager.shared.records import RecordError
from wealth_manager.modules.cashflows.domain import Cashflow
from .domain import replay, validate_links, is_cash

class Commands:
    def __init__(self,factory,today):self.factory,self.today=factory,today
    @arithmetic
    def activate(self,aid):
        with self.factory() as uow:
            account=uow.entities.get(aid)
            if uow.ledger.enabled(aid):return
            uow.accounts.get(aid)
            if account.get('closed_at'):raise RecordError('Сначала откройте закрытый счёт')
            data=uow.ledger.data();holdings=[h for h in data['holdings'].values() if h['account_id']==aid]
            if any(h['quantity']<0 or h['avg_price']<0 for h in holdings):raise RecordError('Журнал не поддерживает отрицательные входящие остатки')
            flows=uow.cashflows.through(aid,'9999-12-31')
            if any(f.pending or f.date>self.today().isoformat() for f in flows):raise RecordError('Перед включением журнала завершите ожидаемые и будущие операции')
            baseline=Decimal(0)
            if not any(f.type=='deposit' and f.amount>0 for f in flows):
                rates=uow.valuations.rates()
                for h in holdings:
                    value=rates.to_rub(h['quantity']*(1 if is_cash(h) else h['avg_price']),h['currency'])
                    if value is None:raise RecordError('Для входящих остатков добавьте курсы валют')
                    baseline+=value
                for f in flows:
                    if f.type=='withdrawal':
                        value=rates.to_rub(f.amount,f.currency)
                        if value is None:raise RecordError('Для входящих остатков добавьте курсы валют')
                        baseline+=value
            uow.ledger.activate(aid,self.today().isoformat(),baseline)
            uow.commit()
    @staticmethod
    def rebuild(uow):
        data=uow.ledger.data()
        balances,realized=replay(data['openings'],data['entries'],data['holdings'])
        uow.ledger.update_balances(balances,realized)
    @arithmetic
    def post(self,entry):
        entry.validate()
        with self.factory() as uow:
            old=uow.ledger.find_request(entry.request_id)
            if old:
                if any(old[k]!=v for k,v in asdict(entry).items()):raise RecordError('Идентификатор уже использован для другой операции')
                return old['id']
            data=uow.ledger.data();validate_links(entry,data['holdings'],data['accounts'],self.today().isoformat())
            eid=uow.ledger.insert(entry);self.rebuild(uow)
            h=data['holdings'][entry.holding_id]
            def flow(aid,kind,amount,currency,category=''):
                fid=uow.cashflows.save(Cashflow(aid,entry.date,kind,amount,currency,note=entry.note,category_code=category))
                uow.ledger.link_flow(eid,fid)
            if entry.kind in ('deposit','withdrawal','income','expense'):
                flow(h['account_id'],entry.kind,entry.quantity,h['currency'])
            if entry.kind=='transfer':
                target=data['holdings'][entry.target_id];value=entry.quantity*(1 if is_cash(h) else entry.price)
                flow(h['account_id'],'withdrawal',value,h['currency'])
                flow(target['account_id'],'deposit',value,h['currency'])
            if entry.fee:flow(h['account_id'],'expense',entry.fee,h['currency'],'broker_fee')
            uow.ledger.invalidate_snapshots(eid,self.today().isoformat())
            uow.take_snapshot();uow.commit();return eid
    def cancel(self,eid):
        with self.factory() as uow:
            e=uow.ledger.get(eid)
            if e['voided']:return
            data=uow.ledger.data()
            if any(data['accounts'][data['holdings'][e[k]]['account_id']].get('closed_at') for k in ('holding_id','cash_id','target_id') if e[k]):
                raise RecordError('Сначала откройте закрытый счёт')
            uow.ledger.void(eid,self.today().isoformat());self.rebuild(uow)
            uow.ledger.remove_flows(eid);uow.ledger.invalidate_snapshots(eid,self.today().isoformat())
            uow.take_snapshot();uow.commit()

class Reports:
    def __init__(self,factory):self.factory=factory
    def overview(self):
        with self.factory() as uow:data=uow.ledger.data()
        data['entries'].reverse()
        return data
