class Commands:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def save_entry(self,record):
        record.validate()
        with self.uow_factory() as uow:
            record_id=uow.taxes.save_entry(record);uow.commit();return record_id
    def save_deduction(self,record):
        record.validate()
        with self.uow_factory() as uow:
            record_id=uow.taxes.save_deduction(record);uow.commit();return record_id
    def delete_entry(self,record_id):
        with self.uow_factory() as uow:
            uow.taxes.delete_entry(record_id);uow.commit()
    def delete_deduction(self,record_id):
        with self.uow_factory() as uow:
            uow.taxes.delete_deduction(record_id);uow.commit()
