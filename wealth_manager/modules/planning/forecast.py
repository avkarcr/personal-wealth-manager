"""Monthly scenario model: pure Decimal arithmetic, no market assumptions or I/O."""
import calendar
from datetime import date
from decimal import Decimal, ROUND_CEILING
from wealth_manager.shared.decimal import arithmetic, decimal, input_decimal

HORIZON_MONTHS=600


def month_date(start,months):
    year,month=divmod(start.month-1+months,12)
    year+=start.year;month+=1
    return date(year,month,min(start.day,calendar.monthrange(year,month)[1]))


@arithmetic
def forecast(goal,current,today,annual_return=None):
    target=decimal(goal['target_amount']);contribution=decimal(goal.get('monthly_contribution',0))
    annual=decimal(goal.get('annual_return',0) if annual_return is None else annual_return)
    inflation=decimal(goal.get('annual_inflation',0))
    step=decimal(goal.get('contribution_step','0.01'))
    deadline=date.fromisoformat(goal['due_date']) if goal.get('due_date') else None
    horizon=month_date(today,HORIZON_MONTHS)
    result=dict(state='ready',date=today.isoformat(),deadline=goal.get('due_date',''),horizon=horizon.isoformat(),
                annual_return=annual,annual_inflation=inflation,monthly_contribution=contribution,
                reached_date=None,required_monthly=None,projected=None,target_at_deadline=None,
                shortfall=None,on_track=None,months=None,contributions=None,timeline=[],
                deadline_supported=bool(deadline and deadline<=horizon),expired=bool(deadline and deadline<today))
    try:
        for value in (target,contribution,annual,inflation,step):input_decimal(value)
    except ValueError:return dict(result,state='invalid',gap_now=None)
    if step<Decimal("1e-18") or target<=0 or contribution<0 or not -99<=annual<=100 or not 0<=inflation<=100:
        return dict(result,state='invalid',gap_now=None)
    if current is None:return dict(result,state='missing',gap_now=None)
    current=decimal(current)
    if current<0:return dict(result,state='negative',gap_now=None)
    if current>Decimal('1e60'):return dict(result,state='invalid',gap_now=None)
    result['gap_now']=max(target-current,Decimal(0))
    if current>=target:result['reached_date']=today.isoformat()
    g=(1+annual/100)**(Decimal(1)/12)
    t=(1+inflation/100)**(Decimal(1)/12)
    n=None
    if result['deadline_supported'] and not result['expired']:
        n=max(0,(deadline.year-today.year)*12+deadline.month-today.month)
        if n and month_date(today,n)>deadline:n-=1
        result['months']=n
        growth=(1+annual/100)**(Decimal(n)/12)
        annuity=Decimal(n) if g==1 else (growth-1)/(g-1)
        target_due=target*(1+inflation/100)**(Decimal(n)/12)
        projected=current*growth+contribution*annuity
        result.update(projected=projected,target_at_deadline=target_due,shortfall=max(target_due-projected,Decimal(0)),
                      on_track=projected>=target_due,contributions=contribution*n)
        if n:
            needed=max((target_due-current*growth)/annuity,Decimal(0))
            result['required_monthly']=(needed/step).to_integral_value(rounding=ROUND_CEILING)*step
    value=current;future_target=target;first_reached=0 if current>=target else None
    points={0:dict(date=today.isoformat(),month=0,value=current,target=target,contributions=Decimal(0))}
    for month in range(1,HORIZON_MONTHS+1):
        value=value*g+contribution;future_target*=t
        if month%12==0:
            growth=(1+annual/100)**(month//12)
            annuity=Decimal(month) if g==1 else (growth-1)/(g-1)
            value=current*growth+contribution*annuity
            future_target=target*(1+inflation/100)**(month//12)
        if month==n:
            value=result['projected'];future_target=result['target_at_deadline']
        newly_reached=first_reached is None and value>=future_target
        if newly_reached:
            first_reached=month;result['reached_date']=month_date(today,month).isoformat()
        if month%12==0 or month==n or month==1 or newly_reached:
            points[month]=dict(date=month_date(today,month).isoformat(),month=month,value=value,target=future_target,contributions=contribution*month)
    limit=n if n is not None else max(first_reached or 120,12)
    result['timeline']=[p for month,p in sorted(points.items()) if month<=limit]
    return result
