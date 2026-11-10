"""Pure read models. Inputs are detached records, never database connections."""
from collections import defaultdict
from datetime import timedelta
from wealth_manager.modules.valuation.domain import account_metrics, parse_date, xirr
from .labels import KIND_SHORT


def joined_flows(data):
    accounts = {a['id']: a for a in data['accounts']}
    return [dict(f, kind=accounts[f['account_id']]['kind'], acc_name=accounts[f['account_id']]['name'],
                 archived=accounts[f['account_id']]['archived']) for f in data['cashflows']]


def metrics(data, acc, rates, today):
    return account_metrics(acc, rates,
        [h for h in data['holdings'] if h['account_id']==acc['id']],
        [f for f in data['cashflows'] if f['account_id']==acc['id']], today)


def portfolio(data, rates, today, include_archived=False):
    accounts = [a for a in data['accounts'] if a['kind'] != 'freelance' and (include_archived or not a['archived'])]
    accounts.sort(key=lambda a: ({'broker':0,'crypto':1,'realty':2}.get(a['kind'],3),a['name']))
    items, total = [], defaultdict(float)
    by_class, by_cur = defaultdict(float), defaultdict(float)
    all_x = []
    for acc in accounts:
        m = metrics(data, acc, rates, today)
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
    flows = [f for f in joined_flows(data) if not f['archived'] and f['kind'] != 'freelance' and not f['pending']]
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
    all_x.append((today, end_val))
    total["xirr"] = xirr(all_x)
    return items, dict(total), dict(by_class), dict(by_cur)

def history(data, rates, account_id=None):
    """Ряд стоимости по датам; для каждого счёта берётся последнее известное значение (forward fill)."""
    ids = {a['id'] for a in data['accounts'] if not a['archived'] and a['kind'] != 'freelance'}
    rows = [r for r in data['snapshots'] if (r['account_id'] == account_id if account_id else r['account_id'] in ids)]
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

def last_months(today, n=12):
    """Список 'YYYY-MM' за последние n месяцев, включая текущий."""
    t = today
    y, m = t.year, t.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]

def income_report(data, rates, today, account_id=None, months=12, year=None):
    """Сводка доходов: по месяцам и группам, по категориям, по источникам, ожидаемые оплаты."""
    if months < 1:
        raise ValueError('months must be positive')
    rows = sorted((r for r in joined_flows(data) if r['type'] in ('income','expense') and
                   (r['account_id'] == account_id if account_id else not r['archived'])), key=lambda r:r['date'])

    if year:
        month_keys = [f"{year}-{m:02d}" for m in range(1, 13)]
    else:
        month_keys = last_months(today, months)
    start, end = month_keys[0], month_keys[-1]

    groups = [k for k in KIND_SHORT]  # порядок групп для графика
    monthly = {g: [0.0] * len(month_keys) for g in groups}
    idx = {k: i for i, k in enumerate(month_keys)}
    by_cat, by_src = defaultdict(float), defaultdict(float)
    tot = defaultdict(float)
    pending = []
    this_month = today.strftime("%Y-%m")
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
