"""Pure rental reporting calculations with an explicit clock."""
from datetime import timedelta
from wealth_manager.modules.valuation.domain import parse_date

def payment_view(p, rates, today):
    """Доп. поля для отображения платежа: просрочка, эффективный курс, разница с ЦБ."""
    today = today.isoformat()
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


def occupancy(acc, leases, owned, today, days=365):
    """Заполняемость и простои за последние `days` дней (с момента владения, если он позже)."""
    start = today - timedelta(days=days - 1)
    first_lease = min((l['start_date'] for l in leases), default=None)
    since = min(x for x in (owned, first_lease, acc["created_at"]) if x) if (owned or first_lease or acc["created_at"]) else None
    if since and parse_date(since) > start:
        start = min(parse_date(since), today)
    total = (today - start).days + 1
    leases = sorted(leases, key=lambda l: l["start_date"])
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
