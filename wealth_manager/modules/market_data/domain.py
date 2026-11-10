from dataclasses import dataclass
import math
import re

class ProviderError(Exception): pass
class MissingRate(ValueError): pass
class NoExchangeSelected(ValueError): pass


def positive(value):
    value=float(value)
    if not math.isfinite(value) or value<=0: raise ValueError('Rate must be finite and positive')
    return value


def symbol(value):
    value=value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{1,20}',value): raise ValueError('Invalid currency or ticker')
    return value


@dataclass(frozen=True)
class Quote:
    price: float
    currency: str
    provider: str


class ExchangeCatalog:
    """Country-level screening, not a guarantee of eligibility for every service."""
    def __init__(self, data):
        self.reviewed_at=data['reviewed_at']
        self.exchanges=data['exchanges']

    def choices(self,country):
        return [dict(e,status='excluded' if country in e['excluded_countries'] else
                     'limited' if country in e['limited_countries'] else 'check') for e in self.exchanges]

    def validate(self,country,selected):
        if not re.fullmatch('[A-Z]{2}',country): raise ValueError('Use an ISO country code')
        allowed={e['id'] for e in self.choices(country) if e['status']!='excluded'}
        if len(selected)!=len(set(selected)) or not set(selected)<=allowed: raise ValueError('Exchange unavailable for this country')
        return tuple(selected)

    def active(self,country,selected):
        if not country: return ()
        allowed={e['id'] for e in self.choices(country) if e['status']!='excluded'}
        return tuple(e for e in selected if e in allowed)
