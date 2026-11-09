"""Расчёты: стоимость, вложения, прибыль, XIRR, структура портфеля."""
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


class Rates:
    def __init__(self, db):
        self.r = {row["currency"]: row["rate"] for row in db.execute("SELECT currency, rate FROM rates")}
        self.r.setdefault("RUB", 1.0)
        row = db.execute("SELECT value FROM settings WHERE key='base_currency'").fetchone()
        self.base = row["value"] if row and row["value"] in self.r else "RUB"

    def to_rub(self, amount, cur):
        return (amount or 0) * self.r.get(cur, 1.0)

    def to_base(self, amount, cur):
        return self.to_rub(amount, cur) / self.r.get(self.base, 1.0)

    def rub_to_base(self, rub):
        return rub / self.r.get(self.base, 1.0)


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def xirr(flows):
    """flows: [(date, amount)], вложения < 0, получения > 0. Возвращает годовую доходность или None."""
    flows = [(d, a) for d, a in flows if a]
    if len(flows) < 2 or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    flows.sort()
    t0 = flows[0][0]
    if (flows[-1][0] - t0).days < 30:
        return None
    yrs = [((d - t0).days / 365.0, a) for d, a in flows]

    def npv(r):
        return sum(a / (1 + r) ** t for t, a in yrs)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def account_metrics(db, acc, rates, holdings=None, flows=None):
    if holdings is None:
        holdings = db.execute("SELECT * FROM holdings WHERE account_id=?", (acc["id"],)).fetchall()
    if flows is None:
        flows = db.execute("SELECT * FROM cashflows WHERE account_id=?", (acc["id"],)).fetchall()
    flows = [f for f in flows if not f["pending"]]

    by_class = defaultdict(float)
    by_cur = defaultdict(float)
    if acc["kind"] == "realty":
        value = rates.to_base(acc["current_value"], acc["currency"])
        cost = 0.0
        by_class["Недвижимость"] += value
        by_cur[acc["currency"]] += value
    else:
        value = cost = 0.0
        for h in holdings:
            v = rates.to_base(h["quantity"] * h["price"], h["currency"])
            value += v
            cost += rates.to_base(h["quantity"] * h["avg_price"], h["currency"])
            by_class[h["asset_class"]] += v
            by_cur[h["currency"]] += v

    sums = defaultdict(float)
    year_ago = (date.today() - timedelta(days=365)).isoformat()
    income_12m = 0.0
    x_flows = []
    external = acc["kind"] == "realty"  # аренда/расходы по недвижимости идут мимо «стоимости»
    for f in flows:
        amt = rates.to_base(f["amount"], f["currency"])
        sums[f["type"]] += amt
        if f["type"] == "income" and f["date"] >= year_ago:
            income_12m += amt
        d = parse_date(f["date"])
        if f["type"] == "deposit":
            x_flows.append((d, -amt))
        elif f["type"] == "withdrawal":
            x_flows.append((d, amt))
        elif external and f["type"] == "income":
            x_flows.append((d, amt))
        elif external and f["type"] == "expense":
            x_flows.append((d, -amt))

    has_deposits = sums["deposit"] > 0
    invested = sums["deposit"] - sums["withdrawal"] if has_deposits else cost
    profit = value - invested
    if external:
        profit += sums["income"] - sums["expense"]
    x_flows.append((date.today(), value))
    irr = xirr(x_flows) if has_deposits else None

    return {
        "value": value,
        "cost": cost,
        "invested": invested,
        "invested_from_cost": not has_deposits,
        "profit": profit,
        "profit_pct": (profit / invested) if invested > 0 else None,
        "income": sums["income"],
        "expense": sums["expense"],
        "income_12m": income_12m,
        "xirr": irr,
        "by_class": dict(by_class),
        "by_cur": dict(by_cur),
        "positions": len(holdings),
    }


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


def take_snapshot(db, rates):
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


def generate_schedule(db, lease, horizon_days=31):
    """Создаёт недостающие платежи «Ожидается» от начала договора до сегодня (+ ближайший период)."""
    months = FREQ_MONTHS.get(lease["frequency"])
    if not months or not lease["start_date"]:
        return 0
    start = parse_date(lease["start_date"])
    end = parse_date(lease["end_date"]) if lease["end_date"] else None
    limit = date.today() + timedelta(days=horizon_days)
    existing = {r["period_start"] for r in db.execute("SELECT period_start FROM rent_payments WHERE lease_id=?", (lease["id"],))}
    k, created = 0, 0
    while True:
        ps = add_months(start, k * months)
        if ps > limit or (end and ps > end):
            break
        pe = add_months(start, (k + 1) * months) - timedelta(days=1)
        if end and pe > end:
            pe = end
        if ps.isoformat() not in existing:
            db.execute("INSERT INTO rent_payments(lease_id, period_start, period_end, due_date, amount, currency, status) "
                       "VALUES (?,?,?,?,?,?, 'expected')",
                       (lease["id"], ps.isoformat(), pe.isoformat(), due_for(ps, lease["pay_day"]).isoformat(),
                        lease["rent_amount"], lease["currency"]))
            created += 1
        k += 1
        if k > 600:
            break
    return created


def sync_rent_cashflow(db, pay_id):
    """Держит в журнале операций запись о доходе, соответствующую платежу по аренде."""
    p = db.execute("SELECT p.*, l.account_id, l.tenant, l.agent FROM rent_payments p JOIN leases l ON l.id=p.lease_id "
                   "WHERE p.id=?", (pay_id,)).fetchone()
    cf = db.execute("SELECT id FROM cashflows WHERE rent_payment_id=?", (pay_id,)).fetchone()
    if not p or p["status"] == "expected":
        if cf:
            db.execute("DELETE FROM cashflows WHERE id=?", (cf["id"],))
        return
    period = f'{parse_date(p["period_start"]).strftime("%d.%m.%y")}–{parse_date(p["period_end"]).strftime("%d.%m.%y")}'
    if p["status"] == "agent":
        row = (p["account_id"], p["tenant_paid_date"] or p["due_date"], "income", p["amount"], p["currency"], "Аренда",
               p["tenant"], 1, f'У риелтора{": " + p["agent"] if p["agent"] else ""} · за {period}')
    else:
        amt = p["received_amount"] or p["amount"]
        cur = p["received_currency"] or p["currency"]
        note = f"за {period}"
        if cur != p["currency"] and p["amount"]:
            note += f' · {p["amount"]:g} {p["currency"]} по курсу {amt / p["amount"]:.2f}'.replace(".", ",")
        row = (p["account_id"], p["received_date"] or p["tenant_paid_date"] or p["due_date"], "income", amt, cur,
               "Аренда", p["tenant"], 0, note)
    if cf:
        db.execute("UPDATE cashflows SET account_id=?, date=?, type=?, amount=?, currency=?, category=?, source=?, pending=?, "
                   "note=? WHERE id=?", row + (cf["id"],))
    else:
        db.execute("INSERT INTO cashflows(account_id, date, type, amount, currency, category, source, pending, note, "
                   "rent_payment_id) VALUES (?,?,?,?,?,?,?,?,?,?)", row + (pay_id,))


def payment_view(p, rates):
    """Доп. поля для отображения платежа: просрочка, эффективный курс, разница с ЦБ."""
    today = date.today().isoformat()
    d = dict(p)
    d["overdue"] = p["status"] == "expected" and p["due_date"] < today
    d["eff_rate"] = None
    d["fx_diff"] = None
    if p["status"] == "received" and p["received_amount"] and p["amount"] and p["received_currency"] != p["currency"]:
        d["eff_rate"] = p["received_amount"] / p["amount"]
        if p["received_currency"] == "RUB" and p["cb_rate"]:
            d["fx_diff"] = p["received_amount"] - p["amount"] * p["cb_rate"]
    d["amount_base"] = rates.to_base(p["amount"], p["currency"])
    return d


def occupancy(db, acc, days=365):
    """Заполняемость и простои за последние `days` дней (с момента владения, если он позже)."""
    today = date.today()
    start = today - timedelta(days=days - 1)
    owned = db.execute("SELECT MIN(date) FROM cashflows WHERE account_id=? AND type='deposit'", (acc["id"],)).fetchone()[0]
    first_lease = db.execute("SELECT MIN(start_date) FROM leases WHERE account_id=?", (acc["id"],)).fetchone()[0]
    since = min(x for x in (owned, first_lease, acc["created_at"]) if x) if (owned or first_lease or acc["created_at"]) else None
    if since and parse_date(since) > start:
        start = parse_date(since)
    total = (today - start).days + 1
    leases = db.execute("SELECT * FROM leases WHERE account_id=? ORDER BY start_date", (acc["id"],)).fetchall()
    iv = []
    for l in leases:
        a = max(parse_date(l["start_date"]), start)
        b = min(parse_date(l["end_date"]) if l["end_date"] else today, today)
        if a <= b:
            iv.append([a, b])
    iv.sort()
    merged = []
    for a, b in iv:
        if merged and a <= merged[-1][1] + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    occupied = sum((b - a).days + 1 for a, b in merged)
    # сегменты для полосы-таймлайна и список простоев
    segs, gaps, cur = [], [], start
    for a, b in merged:
        if a > cur:
            gaps.append((cur, a - timedelta(days=1)))
            segs.append(("vacant", cur, a - timedelta(days=1)))
        segs.append(("occupied", a, b))
        cur = b + timedelta(days=1)
    if cur <= today:
        gaps.append((cur, today))
        segs.append(("vacant", cur, today))
    timeline = [{"kind": k, "from": a.isoformat(), "to": b.isoformat(), "days": (b - a).days + 1,
                 "pct": ((b - a).days + 1) / total * 100} for k, a, b in segs]
    active = [l for l in leases if parse_date(l["start_date"]) <= today and (not l["end_date"] or parse_date(l["end_date"]) >= today)]
    upcoming = [l for l in leases if parse_date(l["start_date"]) > today]
    return {
        "total_days": total, "occupied": occupied, "vacant": total - occupied,
        "rate": occupied / total if total else 0,
        "gaps": [{"from": a.isoformat(), "to": b.isoformat(), "days": (b - a).days + 1} for a, b in gaps],
        "timeline": timeline, "start": start.isoformat(),
        "active": active[0] if active else None, "upcoming": upcoming[0] if upcoming else None,
        "vacant_since": gaps[-1][0].isoformat() if gaps and gaps[-1][1] == today else None,
    }
