"""Catalog mutations share the existing vault unit of work, never monetary entries."""
from dataclasses import asdict, replace
from datetime import date, timedelta
from wealth_manager.shared.ports import UowFactory
from wealth_manager.shared.records import RecordError, iso_date
from .domain import find, dated, Listing, overlaps


class Commands:
    def __init__(self,factory: UowFactory,today=date.today):self.factory,self.today=factory,today

    def current_date(self,value):
        dated(value)
        if value and value>self.today().isoformat():raise RecordError('Дата подтверждения не может быть в будущем')

    def save_sector(self,record):
        record.validate()
        with self.factory() as uow:
            rows=uow.market_catalog.read()['sectors']
            if record.id is not None:find(rows,record.id)
            if record.parent_id is not None:
                parent=find(rows,record.parent_id)
                if parent['id']==record.id or parent['parent_id'] is not None or (record.id is not None and any(r['parent_id']==record.id for r in rows)):
                    raise RecordError('Подотрасль должна принадлежать корневой отрасли; циклы запрещены')
                if parent['archived']:raise RecordError('Сначала восстановите отрасль из архива')
            rid=uow.market_catalog.save_sector(record);uow.commit();return rid

    def delete_sector(self,rid):
        with self.factory() as uow:
            data=uow.market_catalog.read();find(data['sectors'],rid)
            if any(r['parent_id']==rid for r in data['sectors']) or any(r['sector_id']==rid for r in data['memberships']):
                raise RecordError('У отрасли есть подотрасли или компании; используйте архив')
            uow.market_catalog.delete_sector(rid);uow.commit()

    def assign_sector(self,cid,sid,primary=False,source='',as_of=''):
        self.current_date(as_of)
        with self.factory() as uow:
            data=uow.market_catalog.read();company=find(data['companies'],cid);sector=find(data['sectors'],sid)
            if company['status']=='archived':raise RecordError('Сначала восстановите запись из архива')
            if sector['archived'] or (sector['parent_id'] and find(data['sectors'],sector['parent_id'])['archived']):raise RecordError('Сначала восстановите отрасль из архива')
            uow.market_catalog.assign_sector(cid,sid,primary,source,as_of);uow.commit()

    def remove_sector(self,cid,sid):
        with self.factory() as uow:
            data=uow.market_catalog.read();find(data['companies'],cid);find(data['sectors'],sid)
            uow.market_catalog.remove_sector(cid,sid);uow.commit()

    def save_security(self,record):
        record.validate();self.current_date(record.as_of)
        with self.factory() as uow:
            data=uow.market_catalog.read();company=find(data['companies'],record.company_id)
            old=find(data['securities'],record.id) if record.id is not None else None
            if old and old['company_id']!=record.company_id:raise RecordError('Эмитента существующего выпуска менять нельзя')
            if not old and company['status']=='archived':raise RecordError('Сначала восстановите запись из архива')
            if old and any(l['security_id']==record.id for l in data['links']) and any(old[k]!=getattr(record,k) for k in ('kind','isin','registration_number','underlying_id')):
                raise RecordError('Сначала удалите связи позиций с выпуском')
            if record.underlying_id is not None:
                underlying=find(data['securities'],record.underlying_id)
                if underlying['id']==record.id or underlying['kind'] not in ('common_share','preferred_share'):
                    raise RecordError('Базовым выпуском расписки должна быть акция')
            if old and record.kind not in ('common_share','preferred_share') and any(r['underlying_id']==record.id for r in data['securities']):
                raise RecordError('Выпуск используется как базовая акция депозитарной расписки')
            rid=uow.market_catalog.save_security(record);uow.commit();return rid

    def delete_security(self,rid):
        with self.factory() as uow:
            data=uow.market_catalog.read();find(data['securities'],rid)
            if any(l['security_id']==rid for l in data['links']) or any(r['underlying_id']==rid for r in data['securities']):
                raise RecordError('Выпуск связан с позициями или расписками; используйте архив')
            uow.market_catalog.delete_security(rid);uow.commit()

    def validate_listing(self,record,data):
        record.validate();self.current_date(record.as_of)
        security=find(data['securities'],record.security_id)
        old=find(data['listings'],record.id) if record.id is not None else None
        if not old and (security['archived'] or find(data['companies'],security['company_id'])['status']=='archived'):
            raise RecordError('Сначала восстановите запись из архива')
        if old:
            if any(old[k].casefold()!=getattr(record,k).casefold() for k in ('exchange','ticker')) or old['security_id']!=record.security_id or (old['board'] and old['board'].casefold()!=record.board.casefold()):
                raise RecordError('Для смены тикера завершите прежний листинг и добавьте новый')
            if old['as_of'] and record.as_of<old['as_of']:raise RecordError('Подтверждение статуса не может быть раньше предыдущего')
        proposed=asdict(record)
        for other in data['listings']:
            if other['id']==record.id:continue
            same=all(other[k].casefold()==proposed[k].casefold() for k in ('exchange','ticker'))
            board=not other['board'] or not record.board or other['board'].casefold()==record.board.casefold()
            if same and board and overlaps(other,proposed):raise RecordError('Такой тикер на этой бирже и режиме уже занят в указанном периоде')

    def save_listing(self,record):
        with self.factory() as uow:
            self.validate_listing(record,uow.market_catalog.read())
            rid=uow.market_catalog.save_listing(record);uow.commit();return rid

    def rename_listing(self,rid,ticker,effective_date,source=''):
        iso_date(effective_date);self.current_date(effective_date)
        with self.factory() as uow:
            data=uow.market_catalog.read();old=find(data['listings'],rid)
            if effective_date=='0001-01-01' or old['valid_to'] or (old['valid_from'] and effective_date<=old['valid_from']) or (old['as_of'] and effective_date<=old['as_of']):
                raise RecordError('Дата смены тикера должна быть позже начала и последнего подтверждения открытого листинга')
            if ticker.casefold()==old['ticker'].casefold():raise RecordError('Укажите новый тикер')
            end=(date.fromisoformat(effective_date)-timedelta(days=1)).isoformat()
            closed=replace(Listing(**old),valid_to=end)
            self.validate_listing(closed,data);uow.market_catalog.save_listing(closed)
            fresh=Listing(old['security_id'],old['exchange'],ticker,old['board'],effective_date,source=source)
            self.validate_listing(fresh,uow.market_catalog.read())
            new_id=uow.market_catalog.save_listing(fresh);uow.commit();return new_id

    def delete_listing(self,rid):
        with self.factory() as uow:
            data=uow.market_catalog.read();listing=find(data['listings'],rid)
            if any(l['security_id']==listing['security_id'] for l in data['links']):raise RecordError('Листинги связанного выпуска сохраняются для истории')
            uow.market_catalog.delete_listing(rid);uow.commit()
