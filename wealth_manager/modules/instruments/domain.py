"""Explicit research identity; tickers alone never establish ownership."""
from dataclasses import dataclass
from typing import Optional
from wealth_manager.shared.records import required, website, RecordError

KINDS={'company':('stocks','bonds'),'crypto':('crypto','stablecoins')}


def compatible(kind,asset_class):
    return asset_class in KINDS.get(kind,())


@dataclass(frozen=True)
class CryptoAsset:
    name: str
    symbol: str
    network: str = ''
    contract: str = ''
    website: str = ''
    note: str = ''
    archived: bool = False
    id: Optional[int] = None

    def validate(self):
        required(self.name);required(self.symbol);website(self.website)
        if self.contract and not self.network:raise RecordError('Для адреса контракта укажите сеть')
