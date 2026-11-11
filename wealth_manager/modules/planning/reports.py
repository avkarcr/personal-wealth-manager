"""Current goal progress, without creating cashflows or duplicating capital."""
from decimal import Decimal
from datetime import date
from wealth_manager.modules.valuation.quality import issues_for, unique_issues
from wealth_manager.shared.errors import EntityNotFound
from .domain import valued_share
from .ports import PlanningReader


class PlanningReports:
    def __init__(self, reader: PlanningReader, today=date.today): self.reader, self.today = reader, today

    def overview(self):
        data = self.reader.read()
        goals = {g['id']: dict(g, target_amount=Decimal(g['target_amount']), portfolios=[]) for g in data['goals']}
        sources = {(s['kind'], s['id']): s for s in data['sources']}
        portfolios = []
        for p in data['portfolios']:
            goal = goals[p['goal_id']]
            rows = []
            for a in data['allocations']:
                if a['portfolio_id'] != p['id']: continue
                key = ('holding', a['holding_id']) if a['holding_id'] is not None else ('asset', a['asset_id'])
                source = sources[key]
                rows.append(dict(a, source=source, percent=Decimal(a['share_bps'])/100,
                                 value=valued_share(source, a['share_bps'], data['rates'], goal['currency']),
                                 issues=issues_for(source,goal['currency'],data['rates'],data.get('rate_dates',{}),self.today())))
            total = None if any(r['value'] is None for r in rows) else sum((r['value'] for r in rows), Decimal(0))
            item = dict(p, goal_name=goal['name'], currency=goal['currency'], rows=rows, total=total,known_total=sum((r['value'] for r in rows if r['value'] is not None),Decimal(0)),
                        valuation_quality=unique_issues(i for r in rows for i in r['issues']))
            portfolios.append(item)
            goal['portfolios'].append(item)
        for goal in goals.values():
            totals = [p['total'] for p in goal['portfolios']]
            goal['total'] = None if None in totals else sum(totals, Decimal(0))
            goal['known_total'] = sum((p['known_total'] for p in goal['portfolios']),Decimal(0))
            goal['valuation_quality'] = unique_issues(i for p in goal['portfolios'] for i in p['valuation_quality'])
            goal['progress'] = None if goal['total'] is None else goal['total']/goal['target_amount']*100
        return dict(goals=list(goals.values()), portfolios=portfolios, sources=data['sources'], allocations=data['allocations'])

    def detail(self, record_id):
        data = self.overview()
        portfolio = next((p for p in data['portfolios'] if p['id']==record_id), None)
        if portfolio is None: raise EntityNotFound(record_id)
        choices = []
        for source in data['sources']:
            if not source['active'] or (source.get('quantity',0)<0 or (source['value'] is not None and source['value'] < 0)): continue
            column = source['kind']+'_id'
            matches = [a for a in data['allocations'] if a[column]==source['id']]
            other = sum(a['share_bps'] for a in matches if a['portfolio_id']!=record_id)
            own = sum(a['share_bps'] for a in matches if a['portfolio_id']==record_id)
            choices.append(dict(source, key=source['kind']+':'+str(source['id']),
                                available=Decimal(10000-other)/100, assigned=Decimal(own)/100))
        return dict(valuation_quality=portfolio['valuation_quality'],portfolio=portfolio, goals=data['goals'], choices=choices)
