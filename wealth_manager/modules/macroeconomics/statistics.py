"""Explicit statistical series passports and atomic, previewed file ingestion."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import calendar
import hashlib
import re
from .data import Indicator,Source,Series,Artifact,Point,text,instant
from wealth_manager.shared.records import RecordError

ERROR='Проверьте показатель, период, единицы и сопоставление столбцов'
MAX_TOTAL=524288
MAX_FILES=5
# Unit and comparison basis are part of the code; indices are never silently reduced by 100.
PROFILES={
 'cpi_mom_sa_index':('ИПЦ SA: предыдущий месяц = 100','Seasonally adjusted CPI: previous month = 100','index',('monthly',),'sa'),
 'cpi_mom_sa_growth':('Инфляция SA месяц к месяцу, %','Seasonally adjusted inflation MoM, %','percent',('monthly',),'sa'),
 'inflation_expectations_12m':('Ожидаемая инфляция в следующие 12 месяцев, %','Expected inflation over the next 12 months, %','percent',('monthly',),'nsa'),
 'cpi_mom_index':('ИПЦ: предыдущий месяц = 100','CPI: previous month = 100','index',('monthly',),'nsa'),
 'cpi_yoy_index':('ИПЦ: тот же месяц прошлого года = 100','CPI: same month last year = 100','index',('monthly',),'nsa'),
 'cpi_mom_growth':('Инфляция месяц к месяцу, %','Inflation MoM, %','percent',('monthly',),'nsa'),
 'cpi_yoy_growth':('Инфляция год к году, %','Inflation YoY, %','percent',('monthly',),'nsa'),
 'core_cpi_mom_index':('Базовый ИПЦ: предыдущий месяц = 100','Core CPI: previous month = 100','index',('monthly',),'nsa'),
 'gdp_volume_yoy_index':('ИФО ВВП: тот же период прошлого года = 100','GDP volume: same period last year = 100','index',('quarterly','annual'),'nsa'),
 'gdp_real_yoy_growth':('Реальный рост ВВП год к году, %','Real GDP growth YoY, %','percent',('quarterly','annual'),'nsa'),
 'gdp_real_qoq_sa_growth':('Реальный рост ВВП квартал к кварталу SA, %','Real GDP growth QoQ SA, %','percent',('quarterly',),'sa'),
 'gdp_real_level':('ВВП в постоянных ценах, рубли','GDP in constant prices, RUB','RUB',('quarterly','annual'),'nsa'),
 'gdp_real_level_sa':('ВВП в постоянных ценах SA, рубли','Seasonally adjusted GDP in constant prices, RUB','RUB',('quarterly',),'sa'),
 'gdp_nominal_level':('ВВП в текущих ценах, рубли','GDP in current prices, RUB','RUB',('quarterly','annual'),'nsa'),
 'industrial_yoy_index':('Промпроизводство: тот же период прошлого года = 100','Industry: same period last year = 100','index',('monthly',),'nsa'),
 'unemployment_rate':('Уровень безработицы, %','Unemployment rate, %','percent',('monthly','quarterly'),'nsa'),
}
MONTHS=('январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь')

def period(label,frequency,year=None):
    """Recognize explicit annual, monthly or quarterly labels; never cumulative periods."""
    label=str(label).strip()
    if year is not None:
        year=int(str(year).strip());part=label.lower().strip()
        if frequency=='monthly':
            part=str(MONTHS.index(part)+1) if part in MONTHS else part
            label=f'{year:04d}-{int(part):02d}'
        elif frequency=='quarterly':
            part=part.replace('квартал','').replace('quarter','').replace('q','').strip()
            part={'i':'1','ii':'2','iii':'3','iv':'4'}.get(part,part)
            label=f'{year:04d}-Q{int(part)}'
        else:label=str(year)
    if frequency=='annual' and re.fullmatch(r'\d{4}',label):start=date(int(label),1,1);end=date(int(label),12,31)
    elif frequency=='monthly' and re.fullmatch(r'\d{4}-\d{2}(?:-01)?',label):
        start=date(int(label[:4]),int(label[5:7]),1);end=date(start.year,start.month,calendar.monthrange(start.year,start.month)[1])
    elif frequency=='quarterly' and re.fullmatch(r'\d{4}-[Qq][1-4]',label):
        month=(int(label[-1])-1)*3+1;start=date(int(label[:4]),month,1);end=date(start.year,month+2,calendar.monthrange(start.year,month+2)[1])
    else:raise RecordError(ERROR)
    return start.isoformat(),end.isoformat()

@dataclass(frozen=True)
class Upload:
    name: str
    raw: bytes

class StatisticsImport:
    def __init__(self,reader,session,today=date.today):self.reader,self.session,self.today=reader,session,today
    def check_files(self,files):
        if not 1<=len(files)<=MAX_FILES or sum(len(f.raw) for f in files)>MAX_TOTAL:raise RecordError('До 5 файлов, суммарно не более 512 КиБ')
        for f in files:text(f.name,200);Artifact(f.name,f.raw,'statistics-file-v1').validate()
    def inspect(self,files,token):
        self.session.check(token);self.check_files(files)
        return [self.reader.inspect(f) for f in files]
    def bundles(self,files,configs):
        self.check_files(files)
        if len(files)!=len(configs):raise RecordError(ERROR)
        bundles=[];summaries=[];keys=set()
        for f,c in zip(files,configs):
            code=c.get('indicator');frequency=c.get('frequency')
            if code not in PROFILES:raise RecordError(ERROR)
            ru,en,unit,frequencies,adjustment=PROFILES[code]
            if frequency not in frequencies:raise RecordError(ERROR)
            method=c.get('methodology','').strip();text(method,500)
            locator=c.get('source_url','').strip();text(locator,1500)
            # This URL is provenance only; it is never fetched or rendered as an active link.
            if not locator.startswith('https://'):raise RecordError(ERROR)
            scale=Decimal(c.get('scale','1'))
            if scale not in (Decimal(1),Decimal(1000),Decimal(1000000),Decimal(1000000000)) or (unit!='RUB' and scale!=1):raise RecordError(ERROR)
            base=int(c['base_year']) if c.get('base_year') else None
            if (code in ('gdp_real_level','gdp_real_level_sa')) != (base is not None):raise RecordError('Для реального ВВП укажите базовый год; для других рядов оставьте поле пустым')
            published=c.get('published_at','').strip()
            if published and (not c.get('publication_confirmed') or instant(published,True)[:10]>self.today().isoformat()):raise RecordError('Подтвердите дату публикации выбранной версии файла или оставьте её неизвестной')
            source=Source('stat_file_'+hashlib.sha256(locator.encode()).hexdigest()[:32],'Статистический файл / Statistical file','file',locator,'User-supplied source; publication date asserted by user when supplied')
            series=Series(code,'RU',code,unit,frequency,scale,adjustment,base,method);series.validate()
            extracted,skipped=self.reader.extract(f,c,frequency)
            points=[]
            for start,end,value in extracted:
                if published and published<end:raise RecordError(ERROR)
                if end>self.today().isoformat():raise RecordError('Отчётный период ещё не завершён')
                if unit=='index' and value<=0 or unit=='RUB' and value<0 or unit=='percent' and value<Decimal(-100):raise RecordError(ERROR)
                if code=='unemployment_rate' and not 0<=value<=100:raise RecordError(ERROR)
                p=Point(start,end,value,published,'date' if published else 'unknown',c.get('status','unknown'))
                p.validate(series)
                key=(series,source.key,start,end)
                if key in keys:raise RecordError('Период повторяется в одном или нескольких файлах. Уберите пересечение')
                keys.add(key);points.append(p)
            if not points or len(points)>10000:raise RecordError('Нет наблюдений в выбранном диапазоне')
            artifact=self.reader.evidence(f,c);artifact.validate()
            bundles.append((Indicator(code,ru,en),source,series,artifact,points))
            summaries.append(dict(name=f.name,series=series,points=points,skipped=skipped,source=locator,published_at=published))
        return bundles,summaries
    def preview(self,files,configs,token):
        self.session.check(token);_,summaries=self.bundles(files,configs)
        with self.session.write(token) as uow:revision=uow.macro_data.revision_token()
        return dict(files=summaries,revision=revision)
    def confirm(self,files,configs,token,revision):
        self.session.check(token);bundles,_=self.bundles(files,configs)
        with self.session.write(token) as uow:
            if revision!=uow.macro_data.revision_token():raise RecordError('Данные изменились после предпросмотра. Повторите предпросмотр')
            ids=[]
            for bundle in bundles:ids.extend(uow.macro_data.ingest(*bundle))
            uow.commit()
        return len(ids)
