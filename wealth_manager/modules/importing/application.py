from wealth_manager.shared.records import RecordError
from wealth_manager.shared.decimal import arithmetic
from .domain import identify, reconcile, revision, ImportRowError
from .ports import StatementParser


class Imports:
    def __init__(self,factory,ledger,parser: StatementParser,today):
        self.factory,self.ledger,self.parser,self.today=factory,ledger,parser,today

    @arithmetic
    def operations(self,raw,source,account_id,*,expected_revision=None):
        rows=self.parser.operations(raw)
        with self.factory() as uow:
            data=uow.ledger.data();current=revision(data)
            if expected_revision is not None and current!=expected_revision:
                raise RecordError('Данные изменились после проверки. Загрузите файл и проверьте его заново')
            result=[];seen=set()
            for row in rows:
                try:
                    entry=identify(row,source,account_id)
                    if entry.request_id in seen:
                        raise RecordError('В файле повторяется внешний ID операции')
                    seen.add(entry.request_id)
                    h=data['holdings'].get(entry.holding_id)
                    if not h or h['account_id']!=account_id or h['currency']!=row.currency:
                        raise RecordError('Позиция или валюта строки не соответствует выбранному счёту')
                    old=uow.ledger.find_request(entry.request_id)
                    if old and old['voided']:
                        raise RecordError('Импортируемая операция уже отменена. Исправление проведите отдельно в журнале')
                    eid=self.ledger.post_in_uow(uow,entry,rebuild=False)
                    result.append(dict(line=row.line,external_id=row.external_id,entry=entry,
                                       name=h['name'],status='existing' if old else 'new',id=eid))
                except RecordError as error:
                    raise ImportRowError(row.line,str(error)) from error
            self.ledger.rebuild(uow)
            if expected_revision is not None:
                uow.take_snapshot();uow.commit()
            # Preview leaves the UoW uncommitted: entries, flows and balance changes roll back.
            return dict(rows=result,revision=current,new=sum(r['status']=='new' for r in result))

    def balances(self,raw,account_id,day):
        statement=self.parser.balances(raw)
        with self.factory() as uow:data=uow.ledger.data()
        return reconcile(data,account_id,day,statement,self.today().isoformat())
