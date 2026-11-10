from dataclasses import dataclass, replace
from typing import Optional
from wealth_manager.shared.validation import finite, valid_date

FLOW_TYPES = frozenset({"deposit", "withdrawal", "income", "expense"})


@dataclass(frozen=True)
class Cashflow:
    account_id: int
    date: str
    type: str = "deposit"
    amount: float = 0
    currency: str = "RUB"
    category: str = ""
    source: str = ""
    pending: bool = False
    note: str = ""
    rent_payment_id: Optional[int] = None
    id: Optional[int] = None

    def normalized(self):
        if self.type not in FLOW_TYPES:
            raise ValueError("Invalid cashflow type")
        finite(self.amount)
        valid_date(self.date)
        return replace(self, amount=abs(self.amount), currency=self.currency.upper(),
                       pending=bool(self.pending and self.type == "income"))
