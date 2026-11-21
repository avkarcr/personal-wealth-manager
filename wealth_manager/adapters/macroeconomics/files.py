"""Bounded CSV/XLSX reader with explicit row/column mapping; no formula evaluation."""
import csv
import base64
import hashlib
import json
from io import BytesIO,StringIO
from pathlib import PurePosixPath
from zipfile import ZipFile,BadZipFile
from datetime import date,datetime
from decimal import Decimal
import openpyxl
from wealth_manager.modules.macroeconomics.statistics import period,ERROR,PROFILES
from wealth_manager.modules.macroeconomics.data import Artifact
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.decimal import input_decimal

ROWS=5000;COLS=128;CELLS=200000

def cell_text(v):
    if v is None:return ''
    if isinstance(v,(date,datetime)):return v.date().isoformat() if isinstance(v,datetime) else v.isoformat()
    return str(v).strip()

class FileReader:
    def tables(self,file):
        name=file.name.lower()
        try:
            if name.endswith('.csv'):
                try:body=file.raw.decode('utf-8-sig')
                except UnicodeDecodeError:body=file.raw.decode('cp1251')
                lines=body.splitlines()
                delimiter=';' if lines and lines[0].count(';')>lines[0].count(',') else ','
                result={'CSV':list(csv.reader(StringIO(body),delimiter=delimiter,strict=True))}
            elif name.endswith('.xlsx'):
                with ZipFile(BytesIO(file.raw)) as z:
                    entries=z.infolist()
                    if len(entries)>500 or sum(x.file_size for x in entries)>8_000_000:raise ValueError('Archive size')
                    if len({x.filename for x in entries})!=len(entries):raise ValueError('Duplicate ZIP member')
                    for x in entries:
                        if x.flag_bits&1 or 'vba' in x.filename.lower() or 'externallinks/' in x.filename.lower():raise ValueError('Active workbook')
                        if PurePosixPath(x.filename).is_absolute() or '..' in PurePosixPath(x.filename).parts:raise ValueError('Path')
                book=openpyxl.load_workbook(BytesIO(file.raw),read_only=True,data_only=False,keep_links=False)
                try:
                    if len(book.worksheets)>12:raise ValueError('Sheet count')
                    result={};cells=0
                    for sheet in book.worksheets:
                        if (sheet.max_row or 0)>ROWS or (sheet.max_column or 0)>COLS:raise ValueError('Dimensions')
                        result[sheet.title]=[]
                        for row in sheet.iter_rows():
                            if len(result[sheet.title])>=ROWS or len(row)>COLS:raise ValueError('Dimensions')
                            cells+=len(row)
                            if cells>CELLS:raise ValueError('Cell count')
                            result[sheet.title].append([cell_text(c.value) for c in row])
                finally:book.close()
            else:raise RecordError('Поддерживаются CSV и XLSX. XLS/PDF сначала преобразуйте в таблицу значений')
            if sum(sum(len(r) for r in table) for table in result.values())>CELLS:raise ValueError('Cell count')
            for table in result.values():
                if len(table)>ROWS or any(len(row)>COLS or any(len(cell)>4000 for cell in row) for row in table):raise ValueError('Table size')
            return result
        except RecordError:raise
        except Exception as error:raise RecordError('Не удалось прочитать таблицу. Проверьте формат, размер и содержимое файла') from error
    def evidence(self,file,config):
        content=json.dumps(dict(filename=file.name,source_sha256=hashlib.sha256(file.raw).hexdigest(),
            content_base64=base64.b64encode(file.raw).decode(),mapping=config),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        return Artifact(file.name,content,'statistics-file-v1','application/json')
    def inspect(self,file):
        tables=self.tables(file)
        candidate=next((code for code in PROFILES if file.name.lower().startswith(code+'.')), '')
        return dict(name=file.name,suggestion=candidate,sheets=[dict(name=name,rows=len(rows),cols=max(map(len,rows),default=0),sample=rows[:16]) for name,rows in tables.items()])
    def extract(self,file,config,frequency):
        tables=self.tables(file)
        try:
            rows=tables[config['sheet']]
            first,last=int(config['first_row']),int(config['last_row'])
            if not 1<=first<=last<=len(rows):raise ValueError('Row range')
            pcol=int(config['period_col'])-1;vcol=int(config['value_col'])-1
            if not 0<=pcol<COLS or not 0<=vcol<COLS:raise ValueError('Column')
            layout=config.get('layout','rows');result=[];skipped=0
            def at(row,col):return row[col].strip() if 0<=col<len(row) else ''
            def add(label,raw,year=None):
                nonlocal skipped
                if raw in ('','-','—','–','…','...'):skipped+=1;return
                if raw.startswith(('=','+','@')):raise ValueError('Formula')
                start,end=period(label,frequency,year)
                value=input_decimal(raw.replace('\xa0','').replace(' ','').replace(',','.'))
                result.append((start,end,value))
            if layout=='rows':
                ycol=int(config.get('year_col') or 0)-1
                if not -1<=ycol<COLS:raise ValueError('Year column')
                for row in rows[first-1:last]:add(at(row,pcol),at(row,vcol),at(row,ycol) if ycol>=0 else None)
            elif layout=='matrix' and frequency in ('monthly','quarterly'):
                header=int(config['year_row']);endcol=int(config['last_value_col'])
                if not 1<=header<first or not vcol<endcol<=COLS:raise ValueError('Matrix range')
                years=[at(rows[header-1],c) for c in range(vcol,endcol)]
                if any(not y.isdigit() or not 1900<=int(y)<=9999 for y in years) or len(set(years))!=len(years):raise ValueError('Years')
                for row in rows[first-1:last]:
                    for col,year in zip(range(vcol,endcol),years):add(at(row,pcol),at(row,col),year)
            else:raise ValueError('Layout')
            return result,skipped
        except (ValueError,KeyError,IndexError,TypeError) as error:raise RecordError(ERROR) from error
