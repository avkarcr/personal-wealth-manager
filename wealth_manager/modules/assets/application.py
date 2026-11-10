from .domain import Asset
from wealth_manager.modules.accounts.domain import Purchase

class SaveAsset:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def execute(self,asset: Asset,purchase=None):
        asset.validate()
        if purchase is not None:purchase.validate()
        with self.uow_factory() as uow:
            result=uow.assets.save(asset)
            if asset.id is None and purchase and purchase.amount > 0:
                uow.assets.record_purchase(result,asset.currency,purchase)
            uow.take_snapshot();uow.commit()
            return result
