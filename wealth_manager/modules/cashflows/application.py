from dataclasses import replace
from wealth_manager.shared.ports import UowFactory, Clock
from wealth_manager.shared.errors import RentalFlowProtected
from .domain import Cashflow


class CashflowCommands:
    def __init__(self, uow_factory: UowFactory, today: Clock):
        self.uow_factory, self.today = uow_factory, today

    @staticmethod
    def ensure_manual(flow):
        if flow.rent_payment_id:
            raise RentalFlowProtected(flow.account_id)

    def save(self, flow: Cashflow):
        flow = flow.normalized()
        self.ensure_manual(flow)
        with self.uow_factory() as uow:
            account=uow.entities.get(flow.account_id)
            if account.get('closed_at') and (flow.pending or flow.date>account['closed_at']):
                raise ValueError('Operation after closure')
            if flow.id is not None: self.ensure_manual(uow.cashflows.get(flow.id))
            record_id = uow.cashflows.save(flow)
            uow.take_snapshot()
            uow.commit()
            return record_id

    def mark_received(self, record_id):
        with self.uow_factory() as uow:
            flow = uow.cashflows.get(record_id)
            self.ensure_manual(flow)
            account=uow.entities.get(flow.account_id)
            if account.get('closed_at') and self.today().isoformat()>account['closed_at']:
                raise ValueError('Operation after closure')
            uow.cashflows.save(replace(flow, pending=False, date=self.today().isoformat()))
            uow.commit()

    def delete(self, record_id):
        with self.uow_factory() as uow:
            self.ensure_manual(uow.cashflows.get(record_id))
            uow.cashflows.delete(record_id)
            uow.take_snapshot()
            uow.commit()
