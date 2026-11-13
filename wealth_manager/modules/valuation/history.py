"""Dated FX: exact observations only. No current-rate fallback."""
from .domain import ExchangeRates


def rates_on(rows, day, base='RUB'):
    return ExchangeRates({r['currency']: r['rate'] for r in rows if r['date'] == day}, base)
