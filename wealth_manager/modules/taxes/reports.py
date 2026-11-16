from decimal import Decimal
from wealth_manager.shared.decimal import arithmetic
from wealth_manager.shared.records import year_number,country_code
from .ports import Reader
from .domain import STATUSES, estimate, summarize

class Reports:
    def __init__(self,reader: Reader): self.reader=reader
    @arithmetic
    def overview(self,year=None,country=None):
        if year is not None:year=int(year);year_number(year)
        if country:country=country.upper();country_code(country)
        data=self.reader.read()
        matches=lambda r:(year is None or r['year']==year) and (not country or r['country']==country)
        entries=[]
        for r in data['entries']:
            if not matches(r):continue
            payments=[p for p in data.get('payments',[]) if p['entry_id']==r['id']]
            entries.append(dict(r,estimate=estimate(r),payments=payments,
                paid_total=Decimal(r['paid'])+sum(Decimal(p['amount']) for p in payments if not p['voided']),
                attachments=[d for d in data.get('documents',[]) if d['entry_id']==r['id']]))
        deductions=[dict(r,attachments=[d for d in data.get('documents',[]) if d['deduction_id']==r['id']]) for r in data['deductions'] if matches(r)]
        return dict(entries=entries,deductions=deductions,statuses=STATUSES,
                    summary=summarize([dict(r,paid=r['paid_total']) for r in entries],deductions),
                    selected_year=year,selected_country=country or '',
                    years=sorted({r['year'] for r in data['entries']+data['deductions']},reverse=True))
