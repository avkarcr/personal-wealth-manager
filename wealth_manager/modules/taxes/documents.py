"""Tax evidence and payments: bounded inputs, no storage or HTTP dependencies."""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Optional
from wealth_manager.shared.decimal import decimal, input_decimal
from wealth_manager.shared.records import RecordError, money
from wealth_manager.shared.validation import valid_date

MAX_FILE_BYTES=5*1024*1024
MAX_TOTAL_BYTES=50*1024*1024

@dataclass(frozen=True)
class TaxPayment:
    entry_id: int
    date: str
    amount: Decimal
    kind: str='paid'
    note: str=''
    request_id: str=''
    def __post_init__(self):object.__setattr__(self,'amount',decimal(self.amount))
    def validate(self,today):
        valid_date(self.date);input_decimal(self.amount);money(self.amount)
        if self.amount<=0 or self.kind not in ('paid','withheld') or self.date>today:
            raise RecordError('Проверьте положительную сумму, вид и дату уплаты налога')
        if not self.request_id or len(self.request_id)>100 or len(self.note)>2000:
            raise RecordError('Некорректный идентификатор операции')

@dataclass(frozen=True)
class Evidence:
    filename: str
    content: bytes
    entry_id: Optional[int]=None
    deduction_id: Optional[int]=None
    date: str=''
    number: str=''
    issuer: str=''
    note: str=''

    def prepared(self,today):
        if (self.entry_id is None)==(self.deduction_id is None):raise RecordError('Прикрепите документ к одной налоговой записи или вычету')
        if not 0<len(self.content)<=MAX_FILE_BYTES:raise RecordError('Размер документа должен быть от 1 байта до 5 МБ')
        filename=self.filename.replace('\\','/').split('/')[-1]
        if not filename or len(filename)>200 or any(ord(c)<32 or ord(c)==127 for c in filename):
            raise RecordError('Некорректное имя документа')
        ext=filename.rsplit('.',1)[-1].lower()
        signatures=[(('pdf',),b'%PDF-','application/pdf'),(('png',),b'\x89PNG\r\n\x1a\n','image/png'),(('jpg','jpeg'),b'\xff\xd8\xff','image/jpeg')]
        mime=next((mime for extensions,signature,mime in signatures if ext in extensions and self.content.startswith(signature)),None)
        if mime is None:raise RecordError('Поддерживаются PDF, JPEG и PNG с соответствующим расширением')
        valid_date(self.date,optional=True)
        if self.date>today or len(self.number)>200 or len(self.issuer)>300 or len(self.note)>2000:
            raise RecordError('Проверьте дату и длину реквизитов документа')
        return dict(filename=filename,content=self.content,entry_id=self.entry_id,deduction_id=self.deduction_id,
                    mime=mime,digest=sha256(self.content).hexdigest(),size=len(self.content),date=self.date,
                    number=self.number,issuer=self.issuer,note=self.note)
