"""Pure calculations using legacy numeric semantics; no persistence or transport."""
from collections import defaultdict
from math import isfinite
from wealth_manager.shared.classifications import asset_key
from datetime import datetime, timedelta
from .quality import rate_factor, holding_value

class ExchangeRates:
    def __init__(self, rates, base="RUB"):
        self.r = dict(rates)
        self.r['RUB'] = 1.0
        self.base = base

    def convert(self, amount, source, target):
        if amount is None: return None
        if amount == 0: return 0.0
        factor = rate_factor(self.r, source, target)
        if factor is None: return None
        result = float(amount) * float(factor)
        return result if isfinite(result) else None

    def to_rub(self, amount, cur): return self.convert(amount, cur, 'RUB')
    def to_base(self, amount, cur): return self.convert(amount, cur, self.base)
    def rub_to_base(self, rub): return self.convert(rub, 'RUB', self.base)


def complete_sum(values):
    values = list(values)
    return None if any(value is None for value in values) else sum(values)

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

def account_metrics(acc, rates, holdings, flows, today):
    flows = [f for f in flows if not f["pending"]]

    complete = True
    value_complete = True
    by_class = defaultdict(float)
    by_cur = defaultdict(float)
    if acc["kind"] == "realty":
        value = rates.to_base(acc["current_value"], acc["currency"])
        cost = 0.0
        value_complete = value is not None
        value = value if value is not None else 0.0
        by_class["real_estate"] += value
        by_cur[acc["currency"]] += value
    else:
        value = cost = 0.0
        for h in holdings:
            v = rates.to_base(holding_value(h), h["currency"])
            converted_cost = rates.to_base(h["quantity"] * h["avg_price"], h["currency"])
            if v is None: value_complete = False
            if converted_cost is None: complete = False
            cost += converted_cost if converted_cost is not None else 0.0
            if v is None: continue
            value += v
            by_class[asset_key(h)] += v
            by_cur[h["currency"]] += v

    sums = defaultdict(float)
    year_ago = (today - timedelta(days=365)).isoformat()
    income_12m = 0.0
    x_flows = []
    external = acc["kind"] == "realty"  # аренда/расходы по недвижимости идут мимо «стоимости»
    for f in flows:
        amt = rates.to_base(f["amount"], f["currency"])
        if amt is None:
            complete = False
            continue
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

    complete = complete and value_complete
    has_deposits = any(f["type"] == "deposit" and f["amount"] > 0 for f in flows)
    invested = sums["deposit"] - sums["withdrawal"] if has_deposits else cost
    profit = value - invested
    if external:
        profit += sums["income"] - sums["expense"]
    x_flows.append((today, value))
    irr = xirr(x_flows) if has_deposits else None

    return {
        "value": value,
        "cost": cost,
        "invested": invested,
        "invested_from_cost": not has_deposits,
        "profit": profit if complete else None,
        "complete": complete, "value_complete": value_complete,
        "profit_pct": (profit / invested) if complete and invested > 0 else None,
        "income": sums["income"],
        "expense": sums["expense"],
        "income_12m": income_12m,
        "xirr": irr if complete else None,
        "by_class": dict(by_class),
        "by_cur": dict(by_cur),
        "positions": len(holdings),
    }
