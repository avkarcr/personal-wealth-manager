from dataclasses import asdict
from datetime import date
from wealth_manager.shared.records import RecordError
from .documents import MAX_TOTAL_BYTES

class Commands:
    def __init__(self,uow_factory,today=date.today): self.uow_factory,self.today=uow_factory,today
    def save_entry(self,record):
        record.validate()
        with self.uow_factory() as uow:
            if record.id:
                old=uow.tax_evidence.entry(record.id)
                if uow.tax_evidence.payments(record.id) or uow.tax_evidence.documents(entry_id=record.id):
                    if any(old[k]!=getattr(record,k) for k in ('year','country','currency')):
                        raise RecordError('Год, страну и валюту записи с платежами или документами менять нельзя')
            record_id=uow.taxes.save_entry(record);uow.commit();return record_id
    def save_deduction(self,record):
        record=record.normalized();record.validate()
        if record.received_date>self.today().isoformat():raise RecordError('Дата получения возврата не может быть в будущем')
        with self.uow_factory() as uow:
            if record.id:
                old=uow.tax_evidence.deduction(record.id)
                if uow.tax_evidence.documents(deduction_id=record.id) and any(old[k]!=getattr(record,k) for k in ('year','country','currency')):
                    raise RecordError('Год, страну и валюту записи с платежами или документами менять нельзя')
            record_id=uow.taxes.save_deduction(record);uow.commit();return record_id
    def delete_entry(self,record_id):
        with self.uow_factory() as uow:
            if uow.tax_evidence.documents(entry_id=record_id) or uow.tax_evidence.payments(record_id):
                raise RecordError('Нельзя удалить налоговую запись с документами или историей платежей')
            uow.taxes.delete_entry(record_id);uow.commit()
    def delete_deduction(self,record_id):
        with self.uow_factory() as uow:
            if uow.tax_evidence.documents(deduction_id=record_id):raise RecordError('Сначала удалите вложения вычета')
            uow.taxes.delete_deduction(record_id);uow.commit()
    def pay(self,payment):
        payment.validate(self.today().isoformat())
        with self.uow_factory() as uow:
            uow.tax_evidence.entry(payment.entry_id)
            old=uow.tax_evidence.find_payment(payment.request_id)
            if old:
                if old['voided'] or any(old[k]!=v for k,v in asdict(payment).items()):raise RecordError('Идентификатор уже использован для другой или отменённой записи')
                return old['id']
            pid=uow.tax_evidence.add_payment(payment);uow.commit();return pid
    def cancel_payment(self,pid):
        with self.uow_factory() as uow:
            if uow.tax_evidence.payment(pid)['voided']:return
            uow.tax_evidence.void_payment(pid);uow.commit()
    def attach(self,evidence):
        document=evidence.prepared(self.today().isoformat())
        with self.uow_factory() as uow:
            repo=uow.tax_evidence
            if evidence.entry_id is not None:repo.entry(evidence.entry_id)
            else:repo.deduction(evidence.deduction_id)
            for old in repo.documents(evidence.entry_id,evidence.deduction_id):
                if old['digest']==document['digest']:return old['id']
            if repo.size()+document['size']>MAX_TOTAL_BYTES:raise RecordError('Общий размер вложений превышает 50 МБ')
            did=repo.add_document(document);uow.commit();return did
    def document(self,did):
        with self.uow_factory() as uow:return uow.tax_evidence.document(did)
    def delete_document(self,did):
        with self.uow_factory() as uow:
            uow.tax_evidence.document(did);uow.tax_evidence.delete_document(did);uow.commit()
