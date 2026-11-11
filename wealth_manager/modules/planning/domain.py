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
    def validate(self):
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


def valued_share(source,share_bps,rates,currency):
    if not source['active']:return Decimal('0')
    if source['currency'] == currency:
        return Decimal(str(source['value']))*Decimal(share_bps)/10000
    for code in (source['currency'],currency):
        if code not in rates or not Decimal(str(rates[code])).is_finite() or Decimal(str(rates[code]))<=0:return None
    value=Decimal(str(source['value']))*Decimal(share_bps)/10000
    return value*Decimal(str(rates[source['currency']]))/Decimal(str(rates[currency]))
