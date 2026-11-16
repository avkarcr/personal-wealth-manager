from decimal import Decimal
from wealth_manager.shared.decimal import decimal, arithmetic, rounded
from datetime import timedelta
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.modules.valuation.domain import ExchangeRates, complete_sum
from wealth_manager.modules.valuation.quality import report_quality, holding_value, freshness
from wealth_manager.modules.rentals.metrics import occupancy, payment_view
from wealth_manager.modules.rentals.domain import add_months
from wealth_manager.modules.liabilities.domain import summary as liability_summary
from .domain import portfolio, history, income_report, joined_flows, metrics
from .labels import FREQ_MONTHS, FREQ_LABELS, RENT_STATUS, FLOW_LABELS
from .ports import ReportRepository


def rates_for(data):
    settings = {r['key']: r['value'] for r in data['settings']}
    return ExchangeRates({r['currency']:r['rate'] for r in data['rates']}, settings.get('base_currency','RUB'))


class Reports:
    def __init__(self, repository: ReportRepository, today):
        self.repository, self.today = repository, today

    @arithmetic
    def dashboard(self):
        data = self.repository.read(); rates = rates_for(data); today = self.today()
        items,total,classes,currencies = portfolio(data,rates,today)
        debts=liability_summary(data,rates,today)
        total["liabilities"]=debts["total"]
        total["net_worth"]=total.get("value",0)-debts["total"] if total["value_complete"] and debts["total"] is not None else None
        return dict(debts=debts,valuation_quality=report_quality(data,rates.base,today),items=items,total=total,by_class=sorted(classes.items(),key=lambda x:-x[1]),
                    by_cur=sorted(currencies.items(),key=lambda x:-x[1]),hist=history(data,rates),
                    inc=income_report(data,rates,today),has_any=bool(data['accounts'] or data.get('liabilities')),
                    recent=sorted(joined_flows(data),key=lambda f:(f['date'],f['id']),reverse=True)[:6])

    @arithmetic
    def account(self, aid):
        data=self.repository.read(); rates=rates_for(data); today=self.today()
        acc=next((a for a in data['accounts'] if a['id']==aid),None)
        if acc is None: raise EntityNotFound(aid)
        flows=sorted((f for f in data['cashflows'] if f['account_id']==aid),key=lambda f:(f['date'],f['id']),reverse=True)
        if acc['kind']=='freelance':
            inc=income_report(data,rates,today,account_id=aid)
            return 'freelance.html',dict(acc=acc,flows=sorted(flows,key=lambda f:f['pending'],reverse=True),inc=inc,
                                        tax_est=inc['totals'].get('freelance',0)*(acc['tax_rate'] or 0)/100 if inc['complete'] else None,
                                        valuation_quality=report_quality(data,rates.base,today,{aid}))
        m=metrics(data,acc,rates,today); rows=[]
        for h in sorted((h for h in data['holdings'] if h['account_id']==aid),key=lambda h:(h['asset_class'],h['name'])):
            val=rates.to_base(holding_value(h),h['currency'])
            rows.append(dict(h=h,value=val,share=val/m['value'] if val is not None and m['value'] and m['value_complete'] else None,
                             price_status=freshness(h.get('updated_at'),today),
                             pl=(h['price']-h['avg_price'])*h['quantity'] if holding_value(h) is not None else None,
                             pl_pct=h['price']/h['avg_price']-1 if h['avg_price'] and holding_value(h) is not None else None))
        return 'account.html',dict(valuation_quality=report_quality(data,rates.base,today,{aid}),acc=acc,m=m,rows=rows,flows=flows,hist=history(data,rates,aid),
                                  rent=self.rental(data,acc,rates,today) if acc['kind']=='realty' else None,
                                  by_class=sorted(m['by_class'].items(),key=lambda x:-x[1]))

    @staticmethod
    @arithmetic
    def rental(data,acc,rates,today):
        leases=sorted((l for l in data['leases'] if l['asset_id']==acc['id']),key=lambda l:l['start_date'],reverse=True)
        by_id={l['id']:l for l in leases}
        pays=[payment_view(dict(p,tenant=by_id[p['lease_id']]['tenant'],agent=by_id[p['lease_id']]['agent']),rates,today)
              for p in data['rent_payments'] if p['lease_id'] in by_id]
        pays.sort(key=lambda p:(p['period_start'],p['id']),reverse=True)
        year_ago=(today-timedelta(days=365)).isoformat(); day=today.isoformat()
        summary=dict(overdue=complete_sum(p['amount_base'] for p in pays if p['overdue']),overdue_n=sum(p['overdue'] for p in pays),
                     agent=complete_sum(p['amount_base'] for p in pays if p['status']=='agent'),agent_n=sum(p['status']=='agent' for p in pays),
                     fx_diff=sum(p['fx_diff'] or 0 for p in pays if (p['received_date'] or '')>=year_ago),
                     has_fx=any(p['fx_diff'] is not None for p in pays))
        owned=min((f['date'] for f in data['cashflows'] if f['account_id']==acc['id'] and f['type']=='deposit'),default=None)
        info=[dict(l=l,status='future' if l['start_date']>day else 'ended' if l['end_date'] and l['end_date']<day else 'active',
                   monthly=l['rent_amount']/FREQ_MONTHS[l['frequency']] if l['frequency'] in FREQ_MONTHS else None) for l in leases]
        from wealth_manager.modules.liabilities.domain import summary as debts_for
        debts=debts_for(data,rates,today,acc['id'])
        relevant=[f for f in data['cashflows'] if f['account_id']==acc['id'] and not f['pending'] and year_ago<=f['date']<=day]
        received=complete_sum(rates.to_base(f['amount'],f['currency']) for f in relevant if f['type']=='income')
        expenses=complete_sum(rates.to_base(f['amount'],f['currency']) for f in relevant if f['type']=='expense')
        ids={d['id']:d for d in data.get('liabilities',[]) if d['asset_id']==acc['id']}
        payments=[p for p in data.get('liability_payments',[]) if p['liability_id'] in ids and not p['voided'] and year_ago<=p['date']<=day]
        financing=complete_sum(rates.to_base(p['interest']+p['fee'],ids[p['liability_id']]['currency']) for p in payments)
        principal=complete_sum(rates.to_base(p['principal'],ids[p['liability_id']]['currency']) for p in payments)
        net=received-expenses-financing if all(v is not None for v in (received,expenses,financing)) else None
        value=rates.to_base(acc['current_value'],acc['currency'])
        cash=dict(received=received,expenses=expenses,financing=financing,principal=principal,net=net,
                  after_principal=net-principal if net is not None and principal is not None else None,
                  yield_pct=net/value if net is not None and value and value>0 else None,
                  equity=value-debts['total'] if value is not None and debts['total'] is not None else None)
        return dict(cash=cash,debts=debts,leases=info,payments=pays,summary=summary,occ=occupancy(acc,leases,owned,today),FREQ=FREQ_LABELS,STATUS=RENT_STATUS)

    @arithmetic
    def operations(self, account=None, type=None, year=None):
        data=self.repository.read(); rates=rates_for(data); all_flows=joined_flows(data)
        flows=sorted((f for f in all_flows if (not account or str(f['account_id'])==account) and
                     (not type or f['type']==type) and (not year or f['date'][:4]==year)),key=lambda f:(f['date'],f['id']),reverse=True)
        totals={k:Decimal(0) for k in FLOW_LABELS}; monthly={}; complete=True
        for f in flows:
            if not f['pending'] and f['date']<=self.today().isoformat():
                value=rates.to_base(f['amount'],f['currency'])
                if value is None: complete=False
                else: totals[f['type']]+=value
        start=add_months(self.today().replace(day=1),-12).isoformat()
        for f in all_flows:
            if f['type']=='income' and not f['pending'] and start<=f['date']<=self.today().isoformat():
                m=f['date'][:7]; value=rates.to_base(f['amount'],f['currency'])
                if value is None: complete=False
                else: monthly[m]=monthly.get(m,0)+value
        return dict(complete=complete,valuation_quality=report_quality(data,rates.base,self.today()),flows=flows,totals=totals,years=sorted({f['date'][:4] for f in all_flows},reverse=True),
                    f=dict(account=account,type=type,year=year),monthly=sorted(monthly.items()))

    @arithmetic
    def income(self,year=None):
        data=self.repository.read()
        return dict(valuation_quality=report_quality(data,rates_for(data).base,self.today()),inc=income_report(data,rates_for(data),self.today(),year=year),year=year,
                    years=sorted({f['date'][:4] for f in data['cashflows'] if f['type']=='income'},reverse=True))

    def liabilities(self):
        data=self.repository.read();rates=rates_for(data);today=self.today()
        report=liability_summary(data,rates,today)
        return dict(debts=report,all_debts={d['id']:d for d in data.get('liabilities',[])},
                    payments=sorted(data.get('liability_payments',[]),key=lambda p:(p['date'],p['id']),reverse=True),
                    assets=[a for a in data['accounts'] if a['kind']=='realty'],
                    cash=[dict(h,account=next(a['name'] for a in data['accounts'] if a['id']==h['account_id'])) for h in data['holdings']
                          if h['asset_class_code']=='cash' and any(a['id']==h['account_id'] and a.get('ledger_started_on') and not a.get('closed_at') for a in data['accounts'])])

    def navigation(self):
        data=self.repository.read(); rates=rates_for(data)
        nav=sorted((a for a in data['accounts'] if not a['archived'] and not a.get('closed_at')),key=lambda a:({'broker':0,'crypto':1,'realty':2,'other':3}.get(a['kind'],4),a['name']))
        used={r['currency'] for table in ('accounts','holdings','cashflows','liabilities') for r in data.get(table,[])}
        return dict(base=rates.base,currencies=sorted(set(rates.r)|used|{rates.base}),nav_accounts=nav,
                    nav_financial=[a for a in nav if a['kind'] not in ('realty','freelance')],
                    nav_assets=[a for a in nav if a['kind']=='realty'],
                    nav_invest=[a for a in nav if a['kind']!='freelance'],nav_work=[a for a in nav if a['kind']=='freelance'],
                    missing_rates=sorted(c for c in used if c and rates.to_base(1,c) is None))

    def settings(self):
        data=self.repository.read()
        return dict(historical_rates=sorted(data.get('historical_rates',[]),key=lambda r:(r['date'],r['currency']),reverse=True),rates=sorted(data['rates'],key=lambda r:(r['currency']!='RUB',r['currency'])),
                    archived=sorted((a for a in data['accounts'] if a['archived'] or a.get('closed_at')),key=lambda a:a['name']))

    def export(self):
        data=self.repository.read(); names={a['id']:a['name'] for a in data['accounts']}
        return ([dict(h,acc=names[h['account_id']]) for h in data['holdings']],
                [dict(f,acc=f['acc_name']) for f in sorted(joined_flows(data),key=lambda f:f['date'])])
