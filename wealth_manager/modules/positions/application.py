from dataclasses import replace
from wealth_manager.shared.ports import UowFactory, Clock
from wealth_manager.shared.validation import finite
from .domain import Position


class PositionCommands:
    def __init__(self, uow_factory: UowFactory, today: Clock):
        self.uow_factory, self.today = uow_factory, today

    def save(self, position: Position):
        position = position.normalized(self.today())
        with self.uow_factory() as uow:
            account=uow.entities.get(position.account_id)
            if account.get('closed_at'): raise ValueError('Reopen the account before changing positions')
            uow.accounts.get(position.account_id)
            if position.id is not None:
                old=uow.positions.get(position.id)
                if (old.price,old.currency,old.ticker,old.asset_class_code)==(position.price,position.currency,position.ticker,position.asset_class_code):
                    position=replace(position,updated_at=old.updated_at)
            record_id = uow.positions.save(position)
            uow.take_snapshot()
            uow.commit()
            return record_id

    def delete(self, record_id):
        with self.uow_factory() as uow:
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
