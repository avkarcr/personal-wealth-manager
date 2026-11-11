from wealth_manager.shared.ports import UowFactory
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.validation import finite, valid_date


class EntityCommands:
    def __init__(self, uow_factory: UowFactory):
        self.uow_factory = uow_factory

    def set_value(self, account_id, value):
        finite(value)
        with self.uow_factory() as uow:
            uow.assets.set_value(account_id, value)
            uow.take_snapshot()
            uow.commit()

    def toggle_archived(self, account_id):
        with self.uow_factory() as uow:
            uow.entities.toggle_archived(account_id)
            uow.commit()

    def delete(self, account_id):
        with self.uow_factory() as uow:
            uow.entities.delete(account_id)
            uow.commit()

    def record_history(self, account_id, day, value, currency=''):
        finite(value)
        valid_date(day)
        with self.uow_factory() as uow:
            account = uow.entities.get(account_id)
            rates = uow.valuations.rates()
            value_rub = rates.to_rub(value, currency or account['currency'])
            converted = [rates.to_rub(f.amount, f.currency) for f in uow.cashflows.through(account_id,day)
                         if f.type in ('deposit','withdrawal')]
            if value_rub is None or any(v is None for v in converted):
                raise RecordError('Недостаточно курсов для сохранения оценки')
            invested = sum(rates.to_rub(f.amount,f.currency)*(1 if f.type=='deposit' else -1)
                           for f in uow.cashflows.through(account_id,day) if f.type in ('deposit','withdrawal'))
            uow.valuations.save_history(account_id, day, value_rub, invested or value_rub)
            uow.commit()
