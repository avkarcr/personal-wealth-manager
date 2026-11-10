from dataclasses import replace
from wealth_manager.shared.ports import UowFactory, Clock
from .domain import Lease, RentPayment, rent_cashflow, scheduled_payments


class RentalCommands:
    def __init__(self, uow_factory: UowFactory, today: Clock):
        self.uow_factory, self.today = uow_factory, today

    def _schedule(self, uow, lease):
        existing = {p.period_start for p in uow.rentals.payments(lease.id)}
        payments = scheduled_payments(lease, existing, self.today())
        for payment in payments: uow.rentals.save_payment(payment)
        return len(payments)

    def save_lease(self, lease: Lease, generate_schedule=False):
        lease.validate()
        with self.uow_factory() as uow:
            uow.accounts.get(lease.account_id)
            record_id = uow.rentals.save(lease)
            lease = replace(lease, id=record_id)
            for payment in uow.rentals.payments(record_id):
                if lease.end_date and payment.status == 'expected' and payment.period_start > lease.end_date:
                    uow.cashflows.sync_rent(payment.id, None)
                    uow.rentals.delete_payment(payment.id)
                    continue
                if lease.end_date and payment.period_start <= lease.end_date < payment.period_end:
                    payment = replace(payment, period_end=lease.end_date)
                    uow.rentals.save_payment(payment)
                # Tenant/account/period changes must not leave generated income stale.
                uow.cashflows.sync_rent(payment.id, rent_cashflow(payment, lease))
            count = self._schedule(uow, lease) if generate_schedule else 0
            uow.take_snapshot()
            uow.commit()
            return record_id, count

    def generate_schedule(self, record_id):
        with self.uow_factory() as uow:
            lease = uow.rentals.get(record_id)
            count = self._schedule(uow, lease)
            uow.commit()
            return lease.account_id, count

    def delete_lease(self, record_id):
        with self.uow_factory() as uow:
            lease = uow.rentals.get(record_id)
            for payment in uow.rentals.payments(record_id): uow.cashflows.sync_rent(payment.id, None)
            uow.rentals.delete(record_id)
            uow.take_snapshot()
            uow.commit()
            return lease.account_id

    def save_payment(self, payment: RentPayment):
        with self.uow_factory() as uow:
            lease = uow.rentals.get(payment.lease_id)
            payment = payment.normalized(lease, self.today())
            record_id = uow.rentals.save_payment(payment)
            payment = replace(payment, id=record_id)
            uow.cashflows.sync_rent(record_id, rent_cashflow(payment, lease))
            uow.take_snapshot()
            uow.commit()
            return record_id, lease.account_id

    def delete_payment(self, record_id):
        with self.uow_factory() as uow:
            payment = uow.rentals.get_payment(record_id)
            lease = uow.rentals.get(payment.lease_id)
            uow.cashflows.sync_rent(record_id, None)
            uow.rentals.delete_payment(record_id)
            uow.take_snapshot()
            uow.commit()
            return lease.account_id
