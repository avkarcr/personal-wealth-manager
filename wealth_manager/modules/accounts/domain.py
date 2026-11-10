"""Account rules, independent of storage and transport (legacy kinds retained)."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

KINDS = frozenset({"broker", "crypto", "realty", "other", "freelance"})

from wealth_manager.shared.errors import EntityNotFound

class AccountNotFound(EntityNotFound):
    pass

@dataclass(frozen=True)
class Account:
    name: str
    kind: str = "broker"
    institution: str = ""
    currency: str = "RUB"
    current_value: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    note: str = ""
    id: Optional[int] = None

    def validate(self):
        if self.kind not in KINDS or not self.name.strip() or not self.currency.strip():
            raise ValueError("Invalid account")
        if not self.current_value.is_finite() or not self.tax_rate.is_finite():
            raise ValueError("Amounts must be finite")

@dataclass(frozen=True)
class Purchase:
    amount: Decimal
    date: date

    def validate(self):
        if not self.amount.is_finite() or self.amount < 0:
            raise ValueError("Invalid purchase amount")
