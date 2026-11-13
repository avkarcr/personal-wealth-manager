"""Расчёты: стоимость, вложения, прибыль, XIRR, структура портфеля."""
from wealth_manager.modules.valuation.domain import ExchangeRates, parse_date, xirr
from wealth_manager.modules.valuation.domain import account_metrics as calculate_account_metrics
from wealth_manager.modules.rentals.domain import Lease, add_months, due_for, rent_cashflow, scheduled_payments
from collections import defaultdict
from datetime import date, datetime, timedelta

# Compatibility re-exports; presentation labels have one owner.
from wealth_manager.modules.reporting.labels import (
    KIND_LABELS, KIND_SHORT, INVEST_KINDS, FLOW_LABELS, ASSET_CLASSES,
    STABLECOINS, CATEGORIES, FREQ_LABELS, FREQ_MONTHS, RENT_STATUS,
)


class Rates(ExchangeRates):
    def __init__(self, db):
        rates = {row["currency"]: row["rate"] for row in db.execute("SELECT currency, rate FROM rates")}
        row = db.execute("SELECT value FROM settings WHERE key='base_currency'").fetchone()
        super().__init__(rates, row["value"] if row else "RUB")


def account_metrics(db, acc, rates, holdings=None, flows=None):
    if holdings is None:
        holdings = db.execute("SELECT * FROM holdings WHERE account_id=?", (acc["id"],)).fetchall()
    if flows is None:
        flows = db.execute("SELECT * FROM cashflows WHERE account_id=?", (acc["id"],)).fetchall()
    return calculate_account_metrics(acc, rates, holdings, flows, date.today())


def portfolio(db, rates, include_archived=False):
    from wealth_manager.modules.reporting.domain import portfolio as calculate
    from wealth_manager.adapters.persistence.reports import read_connection
    return calculate(read_connection(db), rates, date.today(), include_archived)


def take_snapshot(db, rates, *, commit=True):
    """Фиксирует сегодняшнюю стоимость всех активных счетов (в RUB)."""
    today = date.today().isoformat()
    rates = ExchangeRates(rates.r, "RUB")
    for acc in db.execute("SELECT * FROM accounts WHERE closed_at='' AND kind<>'freelance'").fetchall():
        m = account_metrics(db, acc, rates)
        if not m["complete"]: continue
        contributions = db.execute("SELECT * FROM cashflows WHERE account_id=? AND pending=0 AND date<=? AND type IN ('deposit','withdrawal')",(acc['id'],today)).fetchall()
        if contributions:
            amounts=[]
            for flow in contributions:
                dated=ExchangeRates(dict(db.execute('SELECT currency,rate FROM historical_rates WHERE date=?',(flow['date'],))))
                amount=dated.to_rub(flow['amount'],flow['currency'])
                amounts.append(None if amount is None else amount*(1 if flow['type']=='deposit' else -1))
            if any(a is None for a in amounts): continue
            m['invested']=sum(amounts)
        db.execute(
            "INSERT INTO snapshots(date, account_id, value_rub, invested_rub) VALUES (?,?,?,?) "
            "ON CONFLICT(date, account_id) DO UPDATE SET value_rub=excluded.value_rub, invested_rub=excluded.invested_rub",
            (today, acc["id"], m["value"], m["invested"]),
        )
    if commit:
        db.commit()


def history(db, rates, account_id=None):
    from wealth_manager.modules.reporting.domain import history as calculate
    from wealth_manager.adapters.persistence.reports import read_connection
    return calculate(read_connection(db), rates, account_id)


# ───────────────────────── доходы (в т.ч. фриланс) ─────────────────────────
def last_months(n=12):
    from wealth_manager.modules.reporting.domain import last_months as calculate
    from wealth_manager.adapters.persistence.reports import read_connection
    return calculate(date.today(), n)


def income_report(db, rates, account_id=None, months=12, year=None):
    from wealth_manager.modules.reporting.domain import income_report as calculate
    from wealth_manager.adapters.persistence.reports import read_connection
    return calculate(read_connection(db), rates, date.today(), account_id, months, year)


def missing_rates(db, rates):
    used = {r[0] for r in db.execute("SELECT DISTINCT currency FROM holdings UNION SELECT DISTINCT currency FROM cashflows "
                                     "UNION SELECT DISTINCT currency FROM accounts")}
    return sorted(c for c in used if c and rates.to_rub(1,c) is None)


# ───────────────────────── аренда ─────────────────────────
def generate_schedule(db, lease, horizon_days=31):
    # Compatibility for existing callers; application commands use the ports directly.
    from wealth_manager.adapters.persistence.operations import SqliteRentalRepository
    repository = SqliteRentalRepository(db)
    entity = Lease(**dict(lease))
    payments = scheduled_payments(entity, {p.period_start for p in repository.payments(entity.id)}, date.today(), horizon_days)
    for payment in payments:
        repository.save_payment(payment)
    return len(payments)


def sync_rent_cashflow(db, pay_id):
    from wealth_manager.adapters.persistence.operations import SqliteRentalRepository, SqliteCashflowRepository
    rentals, flows = SqliteRentalRepository(db), SqliteCashflowRepository(db)
    payment = rentals.find_payment(pay_id)
    expected = rent_cashflow(payment, rentals.get(payment.lease_id)) if payment else None
    flows.sync_rent(pay_id, expected)


def payment_view(p, rates):
    from wealth_manager.modules.rentals.metrics import payment_view as calculate
    return calculate(p, rates, date.today())


def occupancy(db, acc, days=365):
    from wealth_manager.modules.rentals.metrics import occupancy as calculate
    owned = db.execute("SELECT MIN(date) FROM cashflows WHERE account_id=? AND type='deposit'", (acc["id"],)).fetchone()[0]
    leases = db.execute("SELECT * FROM leases WHERE asset_id=? ORDER BY start_date", (acc["id"],)).fetchall()
    return calculate(acc, leases, owned, date.today(), days)
