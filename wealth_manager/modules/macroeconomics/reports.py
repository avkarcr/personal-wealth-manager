from datetime import date
from wealth_manager.shared.records import country_code
from wealth_manager.shared.decimal import arithmetic
from .ports import Reader
from .domain import INDICATORS, summarize

# Review reminders for manual records, not expiry dates or economic signals.
REVIEW_DAYS={'key_rate':90,'refinancing_rate':90,'cpi_yoy':45,'gdp_yoy':150,'yield_2y':14,'yield_10y':14}

class Reports:
    def __init__(self,reader: Reader,today=date.today): self.reader,self.today=reader,today
    @arithmetic
    def overview(self,country=None):
        if country:country=country.upper();country_code(country)
        today=self.today();all_rows=self.reader.read()
        countries=sorted({'RU'}|{r['country'] for r in all_rows}|({country} if country else set()))
        rows=sorted((r for r in all_rows if not country or r['country']==country),key=lambda r:(r['date'],r['id']),reverse=True)
        current=[r for r in rows if r['date']<=today.isoformat()]
        summary=summarize(current)
        for item in summary:
            item['age_days']=(today-date.fromisoformat(item['latest']['date'])).days
            item['review_days']=REVIEW_DAYS[item['indicator']]
            item['needs_review']=item['age_days']>item['review_days']
            item['history']=[r for r in current if (r['country'],r['indicator'])==(item['country'],item['indicator'])]
        missing=[dict(country=c,indicator=i) for c in ([country] if country else countries) for i in INDICATORS
                 if not any(r['country']==c and r['indicator']==i for r in summary)]
        return dict(observations=rows,indicators=INDICATORS,summary=summary,missing=missing,countries=countries,
                    selected_country=country or '',as_of=today.isoformat(),future_count=len(rows)-len(current),
                    review_count=sum(r['needs_review'] for r in summary))
