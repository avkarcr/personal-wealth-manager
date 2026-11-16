from decimal import Decimal
from datetime import date
from wealth_manager.shared.decimal import arithmetic
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.modules.planning.domain import valued_share
from wealth_manager.modules.valuation.quality import issues_for, unique_issues
from .domain import compatible
from .ports import Reader

class Reports:
    def __init__(self,reader: Reader,today=date.today):self.reader,self.today=reader,today
    def overview(self):
        data=self.reader.read()
        return dict(cryptos=sorted(data['cryptos'],key=lambda r:(r['archived'],r['name'].casefold())))
    def links(self):
        data=self.reader.read();result={}
        for link in data['links']:
            kind='company' if link['company_id'] is not None else 'crypto';rid=link[kind+'_id']
            target=next(r for r in data['companies' if kind=='company' else 'cryptos'] if r['id']==rid)
            result[link['holding_id']]=dict(kind=kind,id=rid,name=target['name'])
        return result
    @arithmetic
    def detail(self,kind,rid):
        data=self.reader.read()
        records=data['companies'] if kind=='company' else data['cryptos'] if kind=='crypto' else []
        target=next((r for r in records if r['id']==rid),None)
        if target is None:raise EntityNotFound(rid)
        ids={r['holding_id'] for r in data['links'] if r[kind+'_id']==rid}
        linked_ids={r['holding_id'] for r in data['links']}
        positions=[];choices=[];totals={};quality=[]
        goals={g['id']:g for g in data['goals']};portfolios={p['id']:p for p in data['portfolios']}
        for source in data['sources']:
            if source['kind']!='holding':continue
            if source['id'] not in ids:
                if source['active'] and source['id'] not in linked_ids and compatible(kind,source['asset_class_code']):choices.append(source)
                continue
            allocations=[]
            for a in data['allocations']:
                if a['holding_id']!=source['id']:continue
                p=portfolios[a['portfolio_id']];g=goals[p['goal_id']]
                allocations.append(dict(id=p['id'],name=p['name'],goal_id=g['id'],goal_name=g['name'],percent=Decimal(a['share_bps'])/100,
                                        currency=g['currency'],value=valued_share(source,a['share_bps'],data['rates'],g['currency'])))
                quality.extend(issues_for(source,g['currency'],data['rates'],data['rate_dates'],self.today()))
            value=source['value'] if source['active'] else Decimal(0)
            total=totals.setdefault(source['currency'],dict(known=Decimal(0),complete=True))
            if value is None:total['complete']=False
            else:total['known']+=value
            quality.extend(issues_for(source,source['currency'],data['rates'],data['rate_dates'],self.today()))
            positions.append(dict(source,allocations=allocations,current_value=value))
        return dict(kind=kind,record=target,positions=positions,choices=choices,totals=totals,
                    valuation_quality=unique_issues(quality),owned=any(p['active'] and p['quantity']!=0 for p in positions))
