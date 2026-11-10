from wealth_manager.modules.accounts.domain import Account

class SaveFinancialAccount:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def execute(self,account: Account):
        account.validate()
        with self.uow_factory() as uow:
            result=uow.accounts.save(account)
            uow.take_snapshot();uow.commit()
            return result


class SaveAccount:
    """Compatibility dispatcher for old forms; delegates to the typed use cases."""
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def execute(self,draft,purchase=None):
        from wealth_manager.modules.entities.domain import RecordDraft
        from wealth_manager.modules.assets.domain import Asset
        from wealth_manager.modules.assets.application import SaveAsset
        from wealth_manager.modules.income_sources.domain import IncomeSource
        from wealth_manager.modules.income_sources.application import SaveIncomeSource
        draft.validate()
        if purchase is not None:purchase.validate()
        entity=draft.entity() if isinstance(draft,RecordDraft) else draft
        if isinstance(entity,Asset):return SaveAsset(self.uow_factory).execute(entity,purchase)
        if isinstance(entity,IncomeSource):return SaveIncomeSource(self.uow_factory).execute(entity)
        return SaveFinancialAccount(self.uow_factory).execute(entity)
