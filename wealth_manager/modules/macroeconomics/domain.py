from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from wealth_manager.shared.records import country_code, iso_date, number, required, RecordError

INDICATORS = {
    'key_rate':'Ключевая ставка, %',
    'refinancing_rate':'Ставка рефинансирования, %',
    'cpi_yoy':'ИПЦ, % год к году',
    'gdp_yoy':'ВВП, % год к году',
    'yield_2y':'Доходность гособлигаций 2 года, %',
    'yield_10y':'Доходность гособлигаций 10 лет, %',
}

@dataclass(frozen=True)
class Observation:
    country: str
    indicator: str
    date: str
    value: Decimal
    source: str
    note: str = ''
    id: Optional[int] = None

    def validate(self):
        country_code(self.country)
        if self.indicator not in INDICATORS: raise RecordError('Неизвестный показатель')
        iso_date(self.date)
        number(self.value,Decimal('-1000000'),Decimal('1000000'))
        required(self.source)


def summarize(rows):
    groups = {}
    for row in rows: groups.setdefault((row['country'],row['indicator']),[]).append(row)
    result=[]
    for (country,indicator),history in sorted(groups.items()):
        history=sorted(history,key=lambda r:r['date'],reverse=True)
        latest=history[0]
        previous=history[1] if len(history)>1 else None
        result.append(dict(country=country,indicator=indicator,latest=latest,previous=previous,
                           change=Decimal(latest['value'])-Decimal(previous['value']) if previous else None))
    return result
