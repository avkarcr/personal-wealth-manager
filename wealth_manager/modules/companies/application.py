class Commands:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def save(self,record):
        record.validate()
        with self.uow_factory() as uow:
            record_id=uow.companies.save(record)
            uow.commit()
            return record_id
    def watch(self, record_id):
        with self.uow_factory() as uow:
            if uow.companies.watch(record_id):
                uow.commit()

    def delete(self,record_id):
        with self.uow_factory() as uow:
            uow.companies.delete(record_id)
            uow.commit()
