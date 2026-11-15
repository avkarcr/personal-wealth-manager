"""Version 1 canonical UTF-8 CSV adapter. IDs refer to existing local positions."""
import csv
import io
from decimal import InvalidOperation
from wealth_manager.modules.importing.domain import ImportRow, ImportRowError
from wealth_manager.modules.ledger.domain import Entry
from wealth_manager.shared.decimal import input_decimal
from wealth_manager.shared.records import RecordError

MAX_BYTES=1024*1024
MAX_ROWS=500
OPERATIONS=('external_id','date','kind','holding_id','currency','quantity','price','fee','cash_id','target_id','note')
BALANCES=('holding_id','currency','quantity')


class CsvStatements:
    def read(self,raw,columns):
        if not raw or len(raw)>MAX_BYTES:raise RecordError('Файл должен быть UTF-8 CSV размером до 1 МБ')
        try:
            text=raw.decode('utf-8-sig')
            reader=csv.reader(io.StringIO(text),delimiter=';' if ';' in text.splitlines()[0] else ',',strict=True)
            header=next(reader)
            if tuple(header)!=columns:raise ValueError('header')
            rows=[]
            for cells in reader:
                if not cells:continue
                if len(cells)!=len(columns) or len(rows)>=MAX_ROWS:raise ValueError('shape')
                rows.append((reader.line_num,dict(zip(columns,(c.strip() for c in cells)))))
            if not rows:raise RecordError('Файл не содержит данных')
            return rows
        except (UnicodeError,csv.Error,ValueError,StopIteration,IndexError) as error:
            raise RecordError('Некорректный CSV: используйте шаблон, UTF-8 и не более 500 строк') from error

    def operations(self,raw):
        result=[]
        try:
            for line,r in self.read(raw,OPERATIONS):
                entry=Entry(date=r['date'],kind=r['kind'],holding_id=int(r['holding_id']),
                            quantity=input_decimal(r['quantity']),price=input_decimal(r['price'] or '0'),
                            fee=input_decimal(r['fee'] or '0'),cash_id=int(r['cash_id']) if r['cash_id'] else None,
                            target_id=int(r['target_id']) if r['target_id'] else None,note=r['note'])
                result.append(ImportRow(line,r['external_id'],r['currency'],entry))
        except RecordError:raise
        except (ValueError,InvalidOperation) as error:
            raise ImportRowError(line,'Некорректные числа в CSV: используйте точку без разделителей тысяч') from error
        return sorted(result,key=lambda r:(r.entry.date,r.line))

    def balances(self,raw):
        try:return [(int(r['holding_id']),r['currency'],input_decimal(r['quantity'])) for _,r in self.read(raw,BALANCES)]
        except RecordError:raise
        except (ValueError,InvalidOperation) as error:
            raise RecordError('Некорректные числа в CSV: используйте точку без разделителей тысяч') from error
