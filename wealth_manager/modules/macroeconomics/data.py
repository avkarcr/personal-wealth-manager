"""Versioned source observations, independent of HTTP and storage."""
from dataclasses import dataclass
from datetime import date,datetime,timezone
from decimal import Decimal
import calendar
import re
from typing import Optional
from wealth_manager.shared.decimal import input_decimal
from wealth_manager.shared.records import RecordError,country_code,iso_date

ERROR='Некорректные метаданные макроэкономического ряда'

def utcnow():return datetime.now(timezone.utc).isoformat(timespec='microseconds')

def instant(value,end_of_day=False):
    try:
        if len(value)==10:
            iso_date(value)
            if not end_of_day:raise ValueError()
            value+='T23:59:59.999999+00:00'
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError()
        return result.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError,TypeError,AttributeError,OverflowError):raise RecordError(ERROR)

def text(value,limit=500,required=True):
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):raise RecordError(ERROR)

def code(value):
    if not isinstance(value,str) or not re.fullmatch('[a-z][a-z0-9_]{0,79}',value):raise RecordError(ERROR)

@dataclass(frozen=True)
class Indicator:
    code: str
    name_ru: str
    name_en: str
    def validate(self):code(self.code);text(self.name_ru);text(self.name_en)

@dataclass(frozen=True)
class Source:
    key: str
    title: str
    kind: str='remote'
    url: str=''
    terms: str=''
    def validate(self):
        code(self.key);text(self.title);text(self.url,2000,False);text(self.terms,4000,False)
        if self.kind not in ('remote','file') or self.key=='legacy_manual':raise RecordError(ERROR)

@dataclass(frozen=True)
class Series:
    indicator: str
    country: str
    provider_key: str
    unit: str
    frequency: str
    scale: Decimal=Decimal(1)
    adjustment: str='unknown'
    base_year: Optional[int]=None
    methodology: str='unknown'
    def validate(self):
        code(self.indicator);country_code(self.country);text(self.provider_key);text(self.unit,60);text(self.methodology)
        if self.frequency not in ('daily','monthly','quarterly','annual','unknown') or self.adjustment not in ('sa','nsa','unknown'):raise RecordError(ERROR)
        if input_decimal(self.scale)<=0:raise RecordError(ERROR)
        if self.base_year is not None and (type(self.base_year)!=int or not 1900<=self.base_year<=9999):raise RecordError(ERROR)

@dataclass(frozen=True)
class Artifact:
    locator: str
    content: bytes
    parser_version: str
    media_type: str='application/octet-stream'
    def validate(self):
        text(self.locator,2000);text(self.parser_version,100);text(self.media_type,100)
        if not isinstance(self.content,bytes) or not 0<len(self.content)<=2_000_000:raise RecordError('Размер исходного макрофайла должен быть от 1 байта до 2 МБ')

@dataclass(frozen=True)
class Point:
    period_start: str
    period_end: str
    value: Decimal
    published_at: str=''
    publication_precision: str='unknown'
    status: str='unknown'
    note: str=''
    def validate(self,series):
        iso_date(self.period_start);iso_date(self.period_end);input_decimal(self.value);text(self.note,4000,False)
        start=date.fromisoformat(self.period_start);end=date.fromisoformat(self.period_end)
        if end<start:raise RecordError(ERROR)
        if self.publication_precision not in ('unknown','date','instant') or self.status not in ('unknown','preliminary','final','revised'):raise RecordError(ERROR)
        if self.publication_precision=='unknown':
            if self.published_at:raise RecordError(ERROR)
        else:
            if self.publication_precision=='date':iso_date(self.published_at)
            instant(self.published_at,end_of_day=self.publication_precision=='date')
        if series.frequency=='daily' and start!=end:raise RecordError(ERROR)
        if series.frequency=='monthly' and (start.day!=1 or end!=date(start.year,start.month,calendar.monthrange(start.year,start.month)[1])):raise RecordError(ERROR)
        if series.frequency=='quarterly':
            if start.day!=1 or start.month not in (1,4,7,10) or end!=date(start.year,start.month+2,calendar.monthrange(start.year,start.month+2)[1]):raise RecordError(ERROR)
        if series.frequency=='annual' and (start!=date(start.year,1,1) or end!=date(start.year,12,31)):raise RecordError(ERROR)

class DataCommands:
    def __init__(self,factory):self.factory=factory
    def ingest(self,indicator,source,series,artifact,points):
        indicator.validate();source.validate();series.validate();artifact.validate()
        points=list(points)
        if not points or len(points)>10000 or series.indicator!=indicator.code:raise RecordError(ERROR)
        keys=set()
        for point in points:
            point.validate(series)
            key=(point.period_start,point.period_end)
            if key in keys:raise RecordError('Повтор периода в пакете макроданных')
            keys.add(key)
        with self.factory() as uow:
            result=uow.macro_data.ingest(indicator,source,series,artifact,points)
            uow.commit();return result

class DataReports:
    def __init__(self,reader,clock=utcnow):self.reader,self.clock=reader,clock
    def overview(self,as_of=None,mode='known'):
        if mode not in ('known','latest'):raise RecordError(ERROR)
        cutoff=instant(as_of,True) if as_of else instant(self.clock())
        rows=self.reader.read()
        selected={}
        for row in rows:
            if mode=='known' and row['available_at']>cutoff:continue
            key=row['record_id'];order=(row['available_at'],row['revision_id'])
            if key not in selected or order>(selected[key]['available_at'],selected[key]['revision_id']):selected[key]=row
        return dict(rows=[r for r in selected.values() if not r['deleted'] and r['period_end']<=cutoff[:10]],
                    history=[r for r in rows if mode=='latest' or r['available_at']<=cutoff],as_of=as_of or cutoff,mode=mode)


LABELS={'unknown':'Неизвестно','preliminary':'Предварительные','final':'Окончательные','revised':'Пересмотренные',
        'daily':'День','monthly':'Месяц','quarterly':'Квартал','annual':'Год',
        'sa':'Сезонно скорректировано','nsa':'Без сезонной корректировки',
        'manual':'Ручной ввод','file':'Файл','remote':'Внешний источник',
        'date':'Дата','instant':'Точное время'}


INDICATOR_EN={'key_rate':'Key rate, %','refinancing_rate':'Refinancing rate, %','cpi_yoy':'CPI, % year-on-year',
              'gdp_yoy':'GDP, % year-on-year','yield_2y':'Government bond yield, 2 years, %','yield_10y':'Government bond yield, 10 years, %'}
