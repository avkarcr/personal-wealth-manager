from dataclasses import dataclass, replace
from typing import Optional
from wealth_manager.shared.validation import finite
from wealth_manager.shared.classifications import ASSET_LABELS, ASSET_ALIASES


@dataclass(frozen=True)
class Position:
    account_id: int
    name: str = ""
    ticker: str = ""
    asset_class: str = ""
    currency: str = "RUB"
    quantity: float = 0
    avg_price: float = 0
    price: float = 0
    updated_at: str = ""
    id: Optional[int] = None

    asset_class_code: str = "stocks"

    def normalized(self, today):
        finite(self.quantity, self.avg_price, self.price)
        code=self.asset_class_code
        text=self.asset_class
        if code not in ASSET_LABELS and code!='custom': raise ValueError('Invalid asset class code')
        # Compatibility for callers still supplying a legacy label; explicit custom wins.
        if text and code!='custom':
            code=ASSET_ALIASES.get(text,'custom')
            if code!='custom': text=''
        if code=='custom' and not text.strip(): raise ValueError('Custom asset class is required')
        price = 1.0 if code == "cash" else self.price
        average = 1.0 if code == "cash" else (self.avg_price or price)
        return replace(self, name=self.name or self.ticker or "Позиция",
                       ticker=self.ticker.upper(), price=price, avg_price=average, asset_class=text, asset_class_code=code,
                       updated_at=today.isoformat())
