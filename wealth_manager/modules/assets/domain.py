from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

@dataclass(frozen=True)
class Asset:
    name: str
    currency: str = 'RUB'
    current_value: Decimal = Decimal('0')
    institution: str = ''
    note: str = ''
    id: Optional[int] = None

    def validate(self):
        if not self.name.strip() or not self.currency.strip() or not self.current_value.is_finite():
            raise ValueError('Invalid asset')
