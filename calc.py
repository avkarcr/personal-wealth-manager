"""Расчёты: стоимость, вложения, прибыль, XIRR, структура портфеля."""
from wealth_manager.modules.valuation.domain import ExchangeRates, parse_date, xirr
from wealth_manager.modules.valuation.domain import account_metrics as calculate_account_metrics
from wealth_manager.modules.rentals.domain import Lease, add_months, due_for, rent_cashflow, scheduled_payments
from collections import defaultdict
from datetime import date, datetime, timedelta

KIND_LABELS = {"broker": "Брокерский счёт", "crypto": "Криптобиржа / кошелёк", "realty": "Недвижимость",
               "other": "Прочее", "freelance": "Фриланс"}
KIND_SHORT = {"broker": "Фондовый рынок", "crypto": "Крипто", "realty": "Недвижимость", "other": "Прочее", "freelance": "Фриланс"}
INVEST_KINDS = ("broker", "crypto", "realty", "other")
FLOW_LABELS = {"deposit": "Пополнение", "withdrawal": "Вывод", "income": "Доход", "expense": "Расход"}
ASSET_CLASSES = ["Акции", "Облигации", "Фонды", "Деньги", "Золото", "Крипто", "Стейблкоины", "Прочее"]
STABLECOINS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD"}

# Подсказки категорий в форме операции (можно вводить и свои)
CATEGORIES = {
    "broker": {"income": ["Дивиденды", "Купоны", "Проценты на остаток", "Налоговый вычет (ИИС)"],
               "expense": ["Комиссия брокера", "Налог", "Обслуживание счёта"]},
    "crypto": {"income": ["Стейкинг", "Фарминг / LP", "Аирдроп", "Лаунчпул", "Earn / сбережения", "P2P",
                          "Трейдинг", "Рефералы", "Ноды / валидатор"],
               "expense": ["Комиссия биржи", "Газ (комиссия сети)", "Налог", "Комиссия P2P"]},
    "realty": {"income": ["Аренда", "Посуточная аренда"],
               "expense": ["Коммунальные платежи", "Ремонт", "Налог", "Ипотека — проценты", "Страховка"]},
    "other": {"income": ["Проценты", "Доход"], "expense": ["Комиссия", "Налог"]},
    "freelance": {"income": ["Проект", "Почасовая работа", "Абонентская оплата", "Бонус", "Консультация"],
                  "expense": ["Комиссия площадки", "Налог (НПД)", "Софт и сервисы", "Вывод / конвертация"]},
}


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
    q = ("SELECT * FROM accounts WHERE kind<>'freelance'" + ("" if include_archived else " AND archived=0")
         + " ORDER BY CASE kind WHEN 'broker' THEN 0 WHEN 'crypto' THEN 1 WHEN 'realty' THEN 2 ELSE 3 END, name")
    accounts = db.execute(q).fetchall()
    items, total = [], defaultdict(float)
    by_class, by_cur = defaultdict(float), defaultdict(float)
    all_x = []
    for acc in accounts:
        m = account_metrics(db, acc, rates)
        items.append({"acc": acc, "m": m})
        if acc["archived"]:
            continue
        for k in ("value", "invested", "profit", "income_12m", "income"):
            total[k] += m[k]
        for k, v in m["by_class"].items():
            by_class[k] += v
        for k, v in m["by_cur"].items():
            by_cur[k] += v
    total["profit_pct"] = total["profit"] / total["invested"] if total["invested"] > 0 else None

    # Общий XIRR по всем счетам с пополнениями
    flows = db.execute(
        "SELECT c.*, a.kind FROM cashflows c JOIN accounts a ON a.id=c.account_id "
        "WHERE a.archived=0 AND a.kind<>'freelance' AND c.pending=0"
    ).fetchall()
    dep_accounts = {f["account_id"] for f in flows if f["type"] == "deposit"}
    for f in flows:
        if f["account_id"] not in dep_accounts:
            continue
        amt = rates.to_base(f["amount"], f["currency"])
        d = parse_date(f["date"])
        if f["type"] == "deposit":
            all_x.append((d, -amt))
        elif f["type"] == "withdrawal":
            all_x.append((d, amt))
        elif f["kind"] == "realty" and f["type"] == "income":
            all_x.append((d, amt))
        elif f["kind"] == "realty" and f["type"] == "expense":
            all_x.append((d, -amt))
    end_val = sum(i["m"]["value"] for i in items if i["acc"]["id"] in dep_accounts and not i["acc"]["archived"])
    all_x.append((date.today(), end_val))
    total["xirr"] = xirr(all_x)
    return items, dict(total), dict(by_class), dict(by_cur)


def take_snapshot(db, rates, *, commit=True):
    """Фиксирует сегодняшнюю стоимость всех активных счетов (в RUB)."""
    today = date.today().isoformat()
    base_rate = rates.r.get(rates.base, 1.0)
    for acc in db.execute("SELECT * FROM accounts WHERE archived=0 AND kind<>'freelance'").fetchall():
        m = account_metrics(db, acc, rates)
        db.execute(
            "INSERT INTO snapshots(date, account_id, value_rub, invested_rub) VALUES (?,?,?,?) "
            "ON CONFLICT(date, account_id) DO UPDATE SET value_rub=excluded.value_rub, invested_rub=excluded.invested_rub",
            (today, acc["id"], m["value"] * base_rate, m["invested"] * base_rate),
        )
    if commit:
        db.commit()


def history(db, rates, account_id=None):
    """Ряд стоимости по датам; для каждого счёта берётся последнее известное значение (forward fill)."""
    q = "SELECT s.* FROM snapshots s JOIN accounts a ON a.id=s.account_id WHERE a.archived=0 AND a.kind<>'freelance'"
    args = ()
    if account_id:
        q = "SELECT * FROM snapshots WHERE account_id=?"
        args = (account_id,)
    rows = db.execute(q + " ORDER BY date", args).fetchall()
    dates = sorted({r["date"] for r in rows})
    last = {}
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)
    out = []
    for d in dates:
        for r in by_date[d]:
            last[r["account_id"]] = (r["value_rub"], r["invested_rub"])
        out.append({
            "date": d,
            "value": round(rates.rub_to_base(sum(v for v, _ in last.values())), 2),
            "invested": round(rates.rub_to_base(sum(i for _, i in last.values())), 2),
        })
    return out


# ───────────────────────── доходы (в т.ч. фриланс) ─────────────────────────
def last_months(n=12):
    """Список 'YYYY-MM' за последние n месяцев, включая текущий."""
    t = date.today()
    y, m = t.year, t.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def income_report(db, rates, account_id=None, months=12, year=None):
    """Сводка доходов: по месяцам и группам, по категориям, по источникам, ожидаемые оплаты."""
    q = ("SELECT c.*, a.kind, a.name AS acc_name FROM cashflows c JOIN accounts a ON a.id=c.account_id "
         "WHERE c.type IN ('income','expense')")
    args = []
    if account_id:
        q += " AND c.account_id=?"; args.append(account_id)
    else:
        q += " AND a.archived=0"
    rows = db.execute(q + " ORDER BY c.date", args).fetchall()

    if year:
        month_keys = [f"{year}-{m:02d}" for m in range(1, 13)]
    else:
        month_keys = last_months(months)
    start, end = month_keys[0], month_keys[-1]

    groups = [k for k in KIND_SHORT]  # порядок групп для графика
    monthly = {g: [0.0] * len(month_keys) for g in groups}
    idx = {k: i for i, k in enumerate(month_keys)}
    by_cat, by_src = defaultdict(float), defaultdict(float)
    tot = defaultdict(float)
    pending = []
    this_month = date.today().strftime("%Y-%m")
    for r in rows:
        amt = rates.to_base(r["amount"], r["currency"])
        if r["pending"]:
            if r["type"] == "income":
                pending.append({"row": r, "amount": amt})
                tot["pending"] += amt
            continue
        m = r["date"][:7]
        if not (start <= m <= end):
            continue
        if r["type"] == "expense":
            tot["expense"] += amt
            if r["kind"] == "freelance":
                tot["freelance_expense"] += amt
            continue
        monthly[r["kind"]][idx[m]] += amt
        tot["income"] += amt
        tot["freelance" if r["kind"] == "freelance" else "passive"] += amt
        if m == this_month:
            tot["this_month"] += amt
        by_cat[(r["category"] or "Без категории", r["kind"])] += amt
        by_src[(r["source"] or r["acc_name"], r["kind"])] += amt

    # средний месяц — по месяцам с начала учёта (не больше длины периода)
    first = rows[0]["date"][:7] if rows else end
    active = [k for k in month_keys if k >= first and k <= this_month]
    n_active = max(len(active), 1)
    tot["avg_month"] = tot["income"] / n_active
    tot["freelance_avg"] = tot["freelance"] / n_active

    series = [{"key": g, "name": KIND_SHORT[g], "values": [round(v, 2) for v in monthly[g]]}
              for g in groups if any(monthly[g])]
    return {
        "months": month_keys,
        "series": series,
        "totals": dict(tot),
        "by_cat": sorted(((c, k, v) for (c, k), v in by_cat.items()), key=lambda x: -x[2]),
        "by_src": sorted(((s, k, v) for (s, k), v in by_src.items()), key=lambda x: -x[2]),
        "pending": pending,
    }


def missing_rates(db, rates):
    used = {r[0] for r in db.execute("SELECT DISTINCT currency FROM holdings UNION SELECT DISTINCT currency FROM cashflows "
                                     "UNION SELECT DISTINCT currency FROM accounts")}
    return sorted(c for c in used if c and c not in rates.r)


# ───────────────────────── аренда ─────────────────────────
FREQ_LABELS = {"month": "ежемесячно", "quarter": "раз в квартал", "halfyear": "раз в полгода",
               "year": "раз в год", "day": "посуточно / разовые"}
FREQ_MONTHS = {"month": 1, "quarter": 3, "halfyear": 6, "year": 12}
RENT_STATUS = {"expected": "Ожидается", "agent": "У риелтора", "received": "Получено"}








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
    leases = db.execute("SELECT * FROM leases WHERE account_id=? ORDER BY start_date", (acc["id"],)).fetchall()
    return calculate(acc, leases, owned, date.today(), days)
