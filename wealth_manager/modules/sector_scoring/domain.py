"""Source-labelled aggregates. Corrections append revisions, never invent observations."""
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional
from wealth_manager.modules.macroeconomics.data import instant, text
from wealth_manager.shared.records import RecordError, iso_date, website
from wealth_manager.shared.decimal import exact_text
from .config import METRICS, AUTO, number

DATA_ERROR = 'Некорректный отраслевой показатель: проверьте период, единицы, источник и охват'
STATUSES = dict(valid='Подтверждённое значение', unavailable='Значение отозвано или недоступно', not_meaningful='Экономически неприменимое значение')


@dataclass(frozen=True)
class Observation:
    sector_id: int
    metric: str
    value: Optional[str]
    unit: str
    period_start: str
    period_end: str
    source: str
    methodology: str
    sample_size: int
    population_size: int
    published_at: str = ''
    source_url: str = ''
    status: str = 'valid'

    def validated(self, now):
        if type(self.sector_id)!=int or self.sector_id<=0 or self.metric not in METRICS or self.metric in AUTO: raise RecordError(DATA_ERROR)
        if self.unit!=METRICS[self.metric][2] or self.status not in STATUSES: raise RecordError(DATA_ERROR)
        iso_date(self.period_start); iso_date(self.period_end)
        if not self.period_start<=self.period_end<=now[:10]: raise RecordError(DATA_ERROR)
        for value,minimum in ((self.sample_size,1 if self.status=='valid' else 0),(self.population_size,1)):
            if type(value)!=int or not minimum<=value<=1000000: raise RecordError(DATA_ERROR)
        if self.sample_size>self.population_size: raise RecordError(DATA_ERROR)
        text(self.source,500); text(self.methodology,2000); text(self.source_url,2000,False); website(self.source_url)
        published=instant(self.published_at,True) if self.published_at else ''
        # Date-only publication is conservatively available at the end of that day.
        if published and (published[:10]<self.period_end or published[:10]>now[:10] or (len(self.published_at)>10 and published>now)): raise RecordError(DATA_ERROR)
        value = None
        if self.status=='valid':
            value=number(self.value,-1000000,1000000)
            if self.metric in ('liquidity','volatility','dividend_yield','capex_sales') and value<0: raise RecordError(DATA_ERROR)
            if self.metric in ('currency','commodity') and not -100<=value<=100: raise RecordError(DATA_ERROR)
        elif self.value not in (None,''): raise RecordError(DATA_ERROR)
        return dict(asdict(self),value=exact_text(value) if value is not None else None,published_at=published)


def select_observations(rows, cutoff, mode):
    if mode not in ('known','latest'): raise RecordError(DATA_ERROR)
    selected={}
    for row in rows:
        if row['period_end']>cutoff[:10]: continue
        available=row['published_at'] or row['received_at']
        if mode=='known' and available>cutoff: continue
        key=(row['sector_id'],row['metric'])
        # A revised/withdrawn last period never falls back to an older good value.
        # The append order defines corrections; publication controls eligibility.
        # A later withdrawal must not be undone by a date-only older publication.
        order=(row['period_end'],row['id'])
        if key not in selected or order>selected[key][0]: selected[key]=(order,row)
    return {key:item[1] for key,item in selected.items()}


def usable(row, metric, feature, cutoff, config):
    if row is None: return 'missing'
    if row['status']!='valid': return row['status']
    if (date.fromisoformat(cutoff[:10])-date.fromisoformat(row['period_end'])).days>feature['max_age']: return 'stale'
    if row['sample_size']*100 < number(config['min_sample'])*row['population_size']: return 'sample'
    if metric in ('pe','pbv','ev_ebitda') and number(row['value'])<=0: return 'not_meaningful'
    return ''
