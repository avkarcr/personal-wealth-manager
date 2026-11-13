"""Shared current-valuation policy. No network, storage or historical FX rules."""
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
from wealth_manager.shared.classifications import asset_key

MAX_AGE_DAYS = 7


def rate_factor(rates, source, target):
    if source == target and source: return Decimal(1)
    values = []
    for code in (source, target):
        try:
            value = Decimal(1) if code == 'RUB' else Decimal(str(rates.get(code)))
        except (InvalidOperation, TypeError, ValueError): return None
        if not value.is_finite() or value <= 0: return None
        values.append(value)
    return values[0] / values[1]


def holding_value(holding):
    quantity = holding['quantity']
    price = 1 if asset_key(holding) == 'cash' else holding['price']
    if quantity == 0: return 0
    if price is None or not isfinite(price) or price <= 0: return None
    result = quantity * price
    return result if isfinite(result) else None


def freshness(day, today):
    try: observed = date.fromisoformat(day or '')
    except (TypeError, ValueError): return 'undated'
    age = (today - observed).days
    if age < 0: return 'future'
    return 'stale' if age > MAX_AGE_DAYS else 'fresh'


def issues_for(source, target, rates, dates, today):
    """Source has name, currency, value and optionally quote date / needs_quote."""
    if not source.get('active', True) or source.get('quantity') == 0: return []
    result = []
    if source.get('value') is None:
        result.append(dict(kind='price', name=source['name'], status='missing', date=''))
    elif source.get('needs_quote', False):
        status = freshness(source.get('updated_at'), today)
        if status != 'fresh':
            result.append(dict(kind='price', name=source['name'], status=status, date=source.get('updated_at') or ''))
    if source['currency'] != target and source.get('value') != 0:
        for code in dict.fromkeys((source['currency'], target)):
            if code == 'RUB': continue
            status = 'missing' if rate_factor(rates, code, 'RUB') is None else freshness(dates.get(code), today)
            if status != 'fresh': result.append(dict(kind='rate', name=code, status=status, date=dates.get(code) or ''))
    return result


def unique_issues(issues):
    return [dict(zip(('kind','name','status','date'), row)) for row in
            dict.fromkeys(tuple(i[k] for k in ('kind','name','status','date')) for i in issues)]


def report_quality(data, target, today, account_ids=None):
    accounts = {a['id']: a for a in data['accounts'] if
                (a['id'] in account_ids if account_ids is not None else not a.get('closed_at'))}
    rates = {r['currency']: r['rate'] for r in data['rates']}
    dates = {r['currency']: r.get('updated_at') for r in data['rates']}
    sources = [dict(name=h['name'], currency=h['currency'], value=holding_value(h),quantity=h['quantity'],
                    needs_quote=asset_key(h)!='cash',updated_at=h.get('updated_at'))
               for h in data['holdings'] if h['account_id'] in accounts and accounts[h['account_id']]['kind'] not in ('realty','freelance')]
    sources += [dict(name=a['name'],currency=a['currency'],value=a['current_value'],needs_quote=True,updated_at=data.get('asset_dates',{}).get(a['id']))
                for a in accounts.values() if a['kind']=='realty']
    sources += [dict(name=f['currency'],currency=f['currency'],value=f['amount']) for f in data['cashflows']
                if f['account_id'] in accounts and f['amount']]
    return unique_issues(i for source in sources for i in issues_for(source,target,rates,dates,today))
