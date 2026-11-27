"""Coherent read model for summary, history, diagnostics and update state."""
from calendar import monthrange
from datetime import date
from decimal import Decimal as D
from math import ceil
from wealth_manager.modules.macroeconomics.data import select_rows,instant,utcnow
from wealth_manager.modules.macroeconomics.derived import minus_months
from wealth_manager.modules.economic_cycle.application import Cycle
from wealth_manager.modules.economic_cycle.config import FEATURES,GROUPS
from wealth_manager.modules.economic_cycle.model import PHASES
from wealth_manager.modules.macroeconomics.scheduling import KINDS
from wealth_manager.shared.records import RecordError
from .charts import history,CHARTS
from .ports import Session
from wealth_manager.modules.sector_scoring.application import sector_result

RANGES = {'1':'1 год','3':'3 года','5':'5 лет','10':'10 лет','all':'Вся история'}
ERROR='Некорректные параметры макродашборда'
KPI = [('gdp_yoy','gdp','percent'),('cpi_yoy','cpi','percent'),('key_level','key_rate','percent'),('spread_2','spread','pp')]


def parameters(window,chart,page):
    if window not in RANGES or chart not in CHARTS or type(page)!=int or not 1<=page<=100000:raise RecordError(ERROR)


def anchors(start,end):
    """Quarter-end assessments plus the requested date; no implied phase between them."""
    first=date.fromisoformat(start);last=date.fromisoformat(end);result=[]
    for year in range(first.year,last.year+1):
        for month in (3,6,9,12):
            d=date(year,month,monthrange(year,month)[1])
            if first<=d<last:result.append(d.isoformat())
    step=max(1,ceil(len(result)/240))
    return result[::step]+[end],step


def diagnostics(result,rows,cfg):
    groups=[]
    for code,label in GROUPS.items():
        features=[]
        for key,definition in FEATURES.items():
            if definition[0]!=code:continue
            f=result['features'][key];previous=result['previous_features'][key]
            delta=f['value']-previous['value'] if f['value'] is not None and previous['value'] is not None and f['date']!=previous['date'] else None
            features.append(dict(code=key,label=definition[1],delta=delta,**f))
        supports=sum(f['weight'] for f in features if f['code'] in result['supports']);opposes=sum(f['weight'] for f in features if f['code'] in result['contradictions'])
        signal='off' if not D(cfg['groups'][code]) else 'missing' if not result['covered'][code] else 'support' if supports>opposes else 'against' if opposes>supports else 'mixed'
        groups.append(dict(code=code,label=label,weight=D(cfg['groups'][code]),covered=result['covered'][code],contribution=result['group_contributions'][code],signal=signal,features=features))
    sources=[]
    for role,sid in cfg['series'].items():
        if sid is None:continue
        data=[r for r in rows if r['series_id']==sid];last=max(data,key=lambda r:r['period_end']) if data else None
        sources.append(dict(role=role,id=sid,source=last['source_title'] if last else '',date=last['period_end'] if last else None,
                            received=max((r['retrieved_at'] for r in data),default=None),unknown=any(r['publication_precision']=='unknown' for r in data)))
    return groups,sources


class Dashboard:
    def __init__(self,session: Session,clock=utcnow):self.session,self.clock=session,clock

    def overview(self,token,as_of=None,mode='known',window='5',chart='gdp',page=1):
        parameters(window,chart,page)
        snapshot=self.session.dashboard_snapshot(token);now=instant(self.clock())
        cycle=Cycle(self.session,lambda:now);preview=cycle.from_snapshot(snapshot,as_of,mode);result=preview['result'];cfg=result['config'];cutoff=result['as_of']
        selected = select_rows(snapshot['rows'], cutoff, mode)
        selected=[r for r in selected if r['series_id'] in cfg['series'].values()]
        earliest=min((r['period_end'] for r in selected),default=cutoff[:10])
        start=earliest if window=='all' else minus_months(date.fromisoformat(cutoff[:10]),12*int(window)).isoformat()
        if chart=='phases':
            points=[];dates,step=anchors(max(start,earliest),cutoff[:10])
            for d in dates:
                r=result if d==cutoff[:10] else cycle.from_snapshot(snapshot,d,mode)['result']
                points.append(dict(date=d,value=list(PHASES).index(r['phase'])+1,phase=r['phase'],confidence=r['confidence'],coverage=r['coverage'],as_of=r['as_of'],reason=r['reason'],revisions=[],break_before=True))
            graph=dict(kind=chart,label=CHARTS[chart],unit='',points=points,reason='',axis='phase',start=start,end=cutoff[:10],series=[],sampling_quarters=step)
        else:graph=history(selected,cfg,cutoff,mode,chart,start)
        # Full numeric data stays in the graph/JSON; only the HTML table is paginated.
        count=len(graph['points']);pages=max(1,ceil(count/50));page=min(page,pages)
        table=list(reversed(graph['points']))[(page-1)*50:page*50]
        kpis=[]
        for key,target,unit in KPI:
            f=result['features'][key];p=result['previous_features'][key]
            delta=f['value']-p['value'] if f['value'] is not None and p['value'] is not None and f['date']!=p['date'] else None
            source=[result['series'][role]['source_title'] for role in f['roles'] if role in result['series']]
            kpis.append(dict(code=key,label=FEATURES[key][1],chart=target,unit=unit,delta=delta,previous_date=p['date'],source=list(dict.fromkeys(source)),**f))
        groups,sources=diagnostics(result,selected,cfg)
        refresh=[];state=snapshot['refresh']
        for kind in KINDS:
            runs=[r for r in state['runs'] if r['kind']==kind];schedule=state['schedules'].get(kind,{})
            refresh.append(dict(kind=kind,schedule=schedule,last=runs[-1] if runs else None,last_success=next((r['finished'] for r in reversed(runs) if r['status']=='success'),None),allowed=kind in KINDS[:2] or state['access']!='disabled'))
        sectors = sector_result(snapshot['sectors'], result, selected)
        return dict(result=result,sector_result=sectors,fingerprint=preview['fingerprint'],window=window,chart=chart,graph=graph,table=table,page=page,pages=pages,count=count,
                    kpis=kpis,groups=groups,sources=sources,refresh=refresh,now=now,
                    last_received=max((r['retrieved_at'] for r in selected),default=None),empty=not any(cfg['series'].values()))
