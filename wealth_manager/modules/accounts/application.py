from typing import Callable, Optional
from .domain import Account, Purchase
from .ports import AccountUnitOfWork

class SaveAccount:
    def __init__(self, uow_factory: Callable[[], AccountUnitOfWork]):
        self.uow_factory = uow_factory

    def execute(self, account: Account, purchase: Optional[Purchase] = None) -> int:
        account.validate()
        if purchase is not None:
            purchase.validate()
        with self.uow_factory() as uow:
            account_id = uow.accounts.save(account)
            if account.id is None and account.kind == "realty" and purchase and purchase.amount > 0:
                uow.accounts.record_purchase(account_id, account.currency, purchase)
            uow.take_snapshot()
            uow.commit()
        return account_id
