from wealth_manager.shared.records import RecordError
from wealth_manager.shared.ports import UowFactory
from .domain import compatible

class Commands:
    def __init__(self,uow_factory: UowFactory):self.uow_factory=uow_factory
    def save_crypto(self,record):
        record.validate()
        with self.uow_factory() as uow:
            rid=uow.instruments.save_crypto(record);uow.commit();return rid
    def delete_crypto(self,rid):
        with self.uow_factory() as uow:
            uow.instruments.delete_crypto(rid);uow.commit()
    def link(self,hid,kind,rid,security_id=None):
        with self.uow_factory() as uow:
            from wealth_manager.modules.market_catalog.domain import find,security_class
            catalog=uow.market_catalog.read()
            if kind=='security':
                security_id=rid;rid=find(catalog['securities'],security_id)['company_id'];kind='company'
            position=uow.positions.get(hid)
            if not compatible(kind,position.asset_class_code):raise RecordError('Тип позиции не соответствует выбранной записи')
            previous=uow.instruments.holding_link(hid)
            if previous and previous[kind+'_id']!=rid:raise RecordError('Сначала удалите существующую связь позиции')
            if previous and security_id is None:security_id=previous['security_id']
            if previous and previous['security_id'] is not None and previous['security_id']!=security_id:
                raise RecordError('Сначала удалите существующую связь позиции')
            if security_id is not None:
                security=find(catalog['securities'],security_id)
                if kind!='company' or security['company_id']!=rid:raise RecordError('Выпуск принадлежит другому эмитенту')
                if security_class(security['kind'])!=position.asset_class_code:raise RecordError('Уточните тип выпуска: он должен соответствовать классу позиции')
                if security['archived']:raise RecordError('Сначала восстановите запись из архива')
            target=uow.instruments.target(kind,rid)
            if target.get('archived') or target.get('status')=='archived':raise RecordError('Сначала восстановите запись из архива')
            if uow.entities.get(position.account_id).get('closed_at'):raise RecordError('Сначала откройте закрытый счёт')
            uow.instruments.link(hid,kind,rid,security_id);uow.commit()
    def unlink(self,hid):
        with self.uow_factory() as uow:
            uow.positions.get(hid)
            previous=uow.instruments.holding_link(hid)
            uow.instruments.unlink(hid);uow.commit();return previous
