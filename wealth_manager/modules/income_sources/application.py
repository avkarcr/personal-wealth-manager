from .domain import IncomeSource

class SaveIncomeSource:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def execute(self,source: IncomeSource):
        source.validate()
        with self.uow_factory() as uow:
            result=uow.income_sources.save(source)
            uow.commit()
            return result
