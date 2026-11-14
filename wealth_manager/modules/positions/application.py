from dataclasses import replace
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.ports import UowFactory, Clock
from wealth_manager.shared.validation import finite
from .domain import Position


class PositionCommands:
    def __init__(self, uow_factory: UowFactory, today: Clock):
        self.uow_factory, self.today = uow_factory, today

    def save(self, position: Position):
        submitted_average=position.avg_price
        position = position.normalized(self.today())
        with self.uow_factory() as uow:
            account=uow.entities.get(position.account_id)
            if account.get('closed_at'): raise ValueError('Reopen the account before changing positions')
            uow.accounts.get(position.account_id)
            if position.id is not None:
                old=uow.positions.get(position.id)
                if (old.price,old.currency,old.ticker,old.asset_class_code)==(position.price,position.currency,position.ticker,position.asset_class_code):
                    position=replace(position,updated_at=old.updated_at)
            enabled=uow.ledger.enabled(position.account_id)
            if enabled and position.id is not None and not old.quantity and not submitted_average:
                position=replace(position,avg_price=old.avg_price)
            if enabled:
                if position.id is None:
                    if position.quantity:raise RecordError('Для нового актива журнала укажите нулевой остаток')
                elif any(getattr(old,k)!=getattr(position,k) for k in ('quantity','avg_price','currency','ticker','asset_class_code','account_id')):
                    raise RecordError('Остаток и состав актива изменяются только через журнал')
            elif position.id is not None and uow.ledger.enabled(old.account_id):
                raise RecordError('Остаток и состав актива изменяются только через журнал')
            record_id = uow.positions.save(position)
            if enabled and position.id is None:uow.ledger.register_position(record_id)
            uow.take_snapshot()
            uow.commit()
            return record_id

    def delete(self, record_id):
        with self.uow_factory() as uow:
            old=uow.positions.get(record_id)
            if uow.ledger.enabled(old.account_id):raise RecordError('Позиции журнала сохраняются для истории')
            uow.positions.delete(record_id)
            uow.take_snapshot()
            uow.commit()

    def update_prices(self, account_id, prices):
        for price in prices.values(): finite(price)
        with self.uow_factory() as uow:
            uow.accounts.get(account_id)
            for record_id, price in prices.items():
                uow.positions.set_price(record_id, account_id, price, self.today().isoformat())
            uow.take_snapshot()
            uow.commit()
