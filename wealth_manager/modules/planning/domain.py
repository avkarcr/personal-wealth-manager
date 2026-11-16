from wealth_manager.shared.decimal import arithmetic, decimal, input_decimal
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional
from datetime import date

class PlanningError(ValueError): pass

@dataclass(frozen=True)
class Goal:
    name: str
    target_amount: Decimal
    currency: str = 'RUB'
    due_date: str = ''
    note: str = ''
    id: Optional[int] = None
    monthly_contribution: Decimal = Decimal(0)
    annual_return: Decimal = Decimal(0)
    annual_inflation: Decimal = Decimal(0)
    contribution_step: Decimal = Decimal("0.01")
    def __post_init__(self):
        for key in ('target_amount','monthly_contribution','annual_return','annual_inflation','contribution_step'):
            value=getattr(self,key)
            object.__setattr__(self,key,value if isinstance(value,Decimal) else decimal(value))
    def validate(self):
        try:
            for value in (self.target_amount,self.monthly_contribution,self.annual_return,self.annual_inflation,self.contribution_step):input_decimal(value)
        except ValueError as error:raise PlanningError('Параметры плана должны быть конечными числами в допустимом диапазоне') from error
        if self.contribution_step<Decimal("1e-18"):raise PlanningError('Шаг округления взноса должен быть не меньше 0,000000000000000001')
        if self.monthly_contribution<0 or not -99<=self.annual_return<=100 or not 0<=self.annual_inflation<=100:
            raise PlanningError('Взнос должен быть неотрицательным, доходность — от −99 до 100%, инфляция — от 0 до 100%')
        if not self.name.strip() or not self.currency.strip():raise PlanningError('Укажите название и валюту цели')
        if not self.target_amount.is_finite() or self.target_amount<=0:raise PlanningError('Сумма цели должна быть положительной')
        if self.due_date:
            try:date.fromisoformat(self.due_date)
            except ValueError:raise PlanningError('Некорректная дата цели')

@dataclass(frozen=True)
class Portfolio:
    name: str
    goal_id: int
    note: str = ''
    id: Optional[int] = None
    def validate(self):
        if not self.name.strip() or self.goal_id<=0:raise PlanningError('Укажите название портфеля и цель')


def share_basis_points(percent):
    try: value=Decimal(str(percent))
    except InvalidOperation: raise PlanningError('Некорректная доля актива')
    if not value.is_finite() or value<=0 or value>100 or value*100 != (value*100).to_integral_value():
        raise PlanningError('Доля должна быть от 0,01 до 100 процентов с двумя знаками после запятой')
    return int(value*100)


@arithmetic
def valued_share(source,share_bps,rates,currency):
    from wealth_manager.modules.valuation.quality import rate_factor
    if not source['active']:return Decimal('0')
    if source['value'] is None:return None
    value=Decimal(str(source['value']))*Decimal(share_bps)/10000
    if value == 0:return Decimal('0')
    factor=rate_factor(rates,source['currency'],currency)
    return None if factor is None else value*factor
