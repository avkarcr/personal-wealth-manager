from .domain import PlanningError, share_basis_points

class PlanningCommands:
    def __init__(self,uow_factory): self.uow_factory=uow_factory
    def save_goal(self,goal):
        goal.validate()
        with self.uow_factory() as uow:
            result=uow.planning.save_goal(goal);uow.commit();return result
    def delete_goal(self,record_id):
        with self.uow_factory() as uow:
            uow.planning.delete_goal(record_id);uow.commit()
    def save_portfolio(self,portfolio):
        portfolio.validate()
        with self.uow_factory() as uow:
            uow.planning.goal(portfolio.goal_id)
            result=uow.planning.save_portfolio(portfolio);uow.commit();return result
    def delete_portfolio(self,record_id):
        with self.uow_factory() as uow:
            uow.planning.delete_portfolio(record_id);uow.commit()
    def allocate(self,portfolio_id,kind,record_id,percent):
        share=share_basis_points(percent)
        if kind not in ('holding','asset'):raise PlanningError('Неизвестный тип актива')
        with self.uow_factory() as uow:
            uow.planning.portfolio(portfolio_id)
            source=uow.planning.source(kind,record_id)
            if not source['active'] or (source.get('quantity',0)<0 or (source['value'] is not None and source['value']<0)):raise PlanningError('Нельзя распределить архивный актив или короткую позицию')
            if share+uow.planning.assigned(kind,record_id,portfolio_id)>10000:raise PlanningError('Сумма долей актива превышает 100 процентов')
            uow.planning.allocate(portfolio_id,kind,record_id,share);uow.commit()
    def remove_allocation(self,portfolio_id,record_id):
        with self.uow_factory() as uow:
            uow.planning.portfolio(portfolio_id)
            uow.planning.remove_allocation(portfolio_id,record_id);uow.commit()
