class Commands:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def save(self,record):
        record.validate()
        with self.uow_factory() as uow:
            record_id=uow.macroeconomics.save(record)
            uow.commit()
            return record_id
    def delete(self,record_id):
        with self.uow_factory() as uow:
            uow.macroeconomics.delete(record_id)
            uow.commit()
