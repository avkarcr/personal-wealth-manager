from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

@dataclass(frozen=True)
class IncomeSource:
    name: str
    currency: str = 'RUB'
    tax_rate: Decimal = Decimal('0')
    institution: str = ''
    note: str = ''
    id: Optional[int] = None

    def validate(self):
        if not self.name.strip() or not self.currency.strip() or not self.tax_rate.is_finite() or not 0 <= self.tax_rate <= 100:
            raise ValueError('Invalid income source')
