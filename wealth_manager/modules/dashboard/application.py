"""Compose existing read models without adding balances or triggering writes."""
from datetime import timedelta
from .ports import WealthReports, PlanningReports, TaxReports, MacroReports

class Dashboard:
    def __init__(self,wealth: WealthReports,planning: PlanningReports,taxes: TaxReports,macro: MacroReports,today):
        self.wealth,self.planning,self.taxes,self.macro,self.today=wealth,planning,taxes,macro,today

    def overview(self,country='RU'):
        today=self.today();result=self.wealth.dashboard()
        goals=self.planning.overview()['goals']
        taxes=self.taxes.overview()
        summaries=taxes['summary']
        deadlines=sorted((d for d in result['debts']['rows'] if d['outstanding']>0 and d['due_date'] and d['due_date']<=(today+timedelta(days=30)).isoformat()),key=lambda d:(d['due_date'],d['id']))
        result.update(as_of=today.isoformat(),goal_overview=sorted(goals,key=lambda g:(g['due_date'] or '9999-12-31',g['id'])),
                      tax_year=today.year,tax_overview=[r for r in summaries if r['year']==today.year],
                      older_tax_balances=[r for r in summaries if r['year']<today.year and r['balance']>0],
                      debt_deadlines=deadlines,macro=self.macro.overview(country))
        return result
