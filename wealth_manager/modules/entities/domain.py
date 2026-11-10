"""Compatibility command DTO for pre-v4 clients; not an Account domain entity."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from wealth_manager.modules.accounts.domain import Account
from wealth_manager.modules.assets.domain import Asset
from wealth_manager.modules.income_sources.domain import IncomeSource

@dataclass(frozen=True)
class RecordDraft:
    name: str
    kind: str = 'broker'
    institution: str = ''
    currency: str = 'RUB'
    current_value: Decimal = Decimal('0')
    tax_rate: Decimal = Decimal('0')
    note: str = ''
    id: Optional[int] = None

    def entity(self):
        common=dict(name=self.name,currency=self.currency,institution=self.institution,note=self.note,id=self.id)
        if self.kind=='realty': return Asset(current_value=self.current_value,**common)
        if self.kind=='freelance': return IncomeSource(tax_rate=self.tax_rate,**common)
        return Account(kind=self.kind,**common)

    def validate(self):
        if not self.current_value.is_finite() or not self.tax_rate.is_finite():raise ValueError('Amounts must be finite')
        self.entity().validate()
