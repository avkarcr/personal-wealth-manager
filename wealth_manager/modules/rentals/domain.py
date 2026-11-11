from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional
from wealth_manager.shared.validation import finite, valid_date
from wealth_manager.modules.cashflows.domain import Cashflow

FREQ_MONTHS = {"month": 1, "quarter": 3, "halfyear": 6, "year": 12}
RENT_STATUSES = frozenset({"expected", "agent", "received"})


@dataclass(frozen=True)
class Lease:
    asset_id: int
    tenant: str
    start_date: str
    contact: str = ""
    end_date: str = ""
    rent_amount: float = 0
    currency: str = "RUB"
    frequency: str = "month"
    pay_day: int = 0
    deposit_amount: float = 0
    deposit_currency: str = "RUB"
    agent: str = ""
    agent_fee: str = ""
    utilities: str = ""
    terms: str = ""
    id: Optional[int] = None

    def validate(self):
        if self.frequency not in (*FREQ_MONTHS, "day") or not 0 <= self.pay_day <= 31:
            raise ValueError("Invalid rental frequency or due day")
        valid_date(self.start_date)
        valid_date(self.end_date, optional=True)
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("Lease end precedes start")
        finite(self.rent_amount, self.deposit_amount)


@dataclass(frozen=True)
class RentPayment:
    lease_id: int
    period_start: str
    period_end: str = ""
    due_date: str = ""
    amount: float = 0
    currency: str = ""
    status: str = "expected"
    tenant_paid_date: str = ""
    received_date: str = ""
    received_amount: float = 0
    received_currency: str = "RUB"
    cb_rate: float = 0
    note: str = ""
    id: Optional[int] = None

    def normalized(self, lease, today):
        if self.status not in RENT_STATUSES:
            raise ValueError("Invalid rent payment status")
        finite(self.amount, self.received_amount, self.cb_rate)
        amount, currency = self.amount or lease.rent_amount, self.currency or lease.currency
        received = self.status == "received"
        payment = replace(self, period_end=self.period_end or self.period_start,
                          due_date=self.due_date or self.period_start, amount=amount, currency=currency,
                          received_amount=(self.received_amount or amount) if received else 0,
                          received_currency=(currency if received and not self.received_amount else self.received_currency),
                          received_date=(self.received_date or today.isoformat()) if received else "")
        for value in (payment.period_start, payment.period_end, payment.due_date): valid_date(value)
        for value in (payment.tenant_paid_date, payment.received_date): valid_date(value, optional=True)
        if payment.period_end < payment.period_start:
            raise ValueError("Payment period ends before it starts")
        return payment


def rent_cashflow(payment, lease):
    """A payment owns its generated income; expected payments have no cashflow."""
    if payment.status == "expected":
        return None
    period = (date.fromisoformat(payment.period_start).strftime("%d.%m.%y") + "–" +
              date.fromisoformat(payment.period_end).strftime("%d.%m.%y"))
    if payment.status == "agent":
        return Cashflow(lease.asset_id, payment.tenant_paid_date or payment.due_date, "income",
                        payment.amount, payment.currency, "", lease.tenant, True,
                        f'У риелтора{": " + lease.agent if lease.agent else ""} · за {period}', payment.id, category_code="rent")
    amount = payment.received_amount or payment.amount
    currency = payment.received_currency or payment.currency
    note = f"за {period}"
    if currency != payment.currency and payment.amount:
        note += f' · {payment.amount:g} {payment.currency} по курсу {amount / payment.amount:.2f}'.replace(".", ",")
    return Cashflow(lease.asset_id, payment.received_date or payment.tenant_paid_date or payment.due_date,
                    "income", amount, currency, "", lease.tenant, False, note, payment.id, category_code="rent")


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))

def due_for(period_start, pay_day):
    import calendar
    if not pay_day:
        return period_start
    last = calendar.monthrange(period_start.year, period_start.month)[1]
    return period_start.replace(day=min(int(pay_day), last))

def scheduled_payments(lease, existing_starts, today, horizon_days=31):
    months = FREQ_MONTHS.get(lease.frequency)
    if not months:
        return []
    start = date.fromisoformat(lease.start_date)
    end = date.fromisoformat(lease.end_date) if lease.end_date else None
    limit = today + timedelta(days=horizon_days)
    payments = []
    for k in range(601):  # Preserve the legacy horizon cap.
        ps = add_months(start, k * months)
        if ps > limit or (end and ps > end):
            break
        pe = add_months(start, (k + 1) * months) - timedelta(days=1)
        if end and pe > end:
            pe = end
        if ps.isoformat() not in existing_starts:
            payments.append(RentPayment(lease.id, ps.isoformat(), pe.isoformat(),
                                       due_for(ps, lease.pay_day).isoformat(), lease.rent_amount, lease.currency))
    return payments
