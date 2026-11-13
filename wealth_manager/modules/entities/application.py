from datetime import date
from wealth_manager.shared.ports import UowFactory
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.validation import finite, valid_date


class EntityCommands:
    def __init__(self, uow_factory: UowFactory, today=date.today):
        self.uow_factory, self.today = uow_factory, today

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

    def close(self, account_id):
        with self.uow_factory() as uow:
            account=uow.entities.get(account_id)
            if account.get('closed_at'): return
            if account['kind'] in ('realty','freelance'):
                raise RecordError('Закрытие доступно только для финансовых счетов')
            if not uow.entities.can_close(account_id, self.today().isoformat()):
                raise RecordError('Для закрытия обнулите позиции и завершите ожидаемые и будущие операции')
            uow.entities.set_closed(account_id,self.today().isoformat())
            uow.commit()

    def reopen(self, account_id):
        with self.uow_factory() as uow:
            if not uow.entities.get(account_id).get('closed_at'): return
            uow.entities.set_closed(account_id,'',self.today().isoformat())
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
            if day > self.today().isoformat(): raise RecordError('Историческая оценка не может быть в будущем')
            if account.get('closed_at') and day >= account['closed_at'] and value != 0:
                raise RecordError('После закрытия стоимость счёта должна быть нулевой')
            rates = uow.valuations.historical_rates(day)
            value_rub = rates.to_rub(value, currency or account['currency'])
            converted = [uow.valuations.historical_rates(f.date).to_rub(f.amount, f.currency) for f in uow.cashflows.through(account_id,day)
                         if f.type in ('deposit','withdrawal')]
            if value_rub is None or any(v is None for v in converted):
                raise RecordError('Недостаточно курсов для сохранения оценки')
            relevant = [f for f in uow.cashflows.through(account_id,day) if f.type in ('deposit','withdrawal')]
            invested = sum(v*(1 if f.type=='deposit' else -1) for f,v in zip(relevant,converted))
            uow.valuations.save_history(account_id, day, value_rub, invested if relevant else value_rub)
            uow.commit()
