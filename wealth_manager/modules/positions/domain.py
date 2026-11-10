from dataclasses import dataclass, replace
from typing import Optional
from wealth_manager.shared.validation import finite


@dataclass(frozen=True)
class Position:
    account_id: int
    name: str = ""
    ticker: str = ""
    asset_class: str = "Акции"
    currency: str = "RUB"
    quantity: float = 0
    avg_price: float = 0
    price: float = 0
    updated_at: str = ""
    id: Optional[int] = None

    def normalized(self, today):
        finite(self.quantity, self.avg_price, self.price)
        price = 1.0 if self.asset_class == "Деньги" else self.price
        average = 1.0 if self.asset_class == "Деньги" else (self.avg_price or price)
        return replace(self, name=self.name or self.ticker or "Позиция",
                       ticker=self.ticker.upper(), price=price, avg_price=average,
                       updated_at=today.isoformat())
