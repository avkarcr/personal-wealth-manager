"""Financial account rules; property and income sources are separate entities."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

KINDS = frozenset({"broker", "crypto", "other"})

from wealth_manager.shared.errors import EntityNotFound

class AccountNotFound(EntityNotFound):
    pass

@dataclass(frozen=True)
class Account:
    name: str
    kind: str = "broker"
    institution: str = ""
    currency: str = "RUB"
    note: str = ""
    id: Optional[int] = None

    def validate(self):
        if self.kind not in KINDS or not self.name.strip() or not self.currency.strip():
            raise ValueError("Invalid account")

@dataclass(frozen=True)
class Purchase:
    amount: Decimal
    date: date

    def validate(self):
        if not self.amount.is_finite() or self.amount < 0:
            raise ValueError("Invalid purchase amount")
