"""HTTP forms for global goals and goal-bound portfolios."""
from flask import render_template, redirect, url_for
from wealth_manager.modules.planning.domain import Goal, Portfolio
from .commands import validated, optional_id
from .forms import f_str, f_decimal


def register_planning(app, runtime):
    commands = runtime.services['planning']
    reports = runtime.services['planning_reports']

    @app.get('/portfolios')
    def portfolios():
        return render_template('portfolios.html', **reports.overview(), missing_rates=[])

    @app.get('/portfolios/<int:pid>')
    @validated
    def portfolio_detail(pid):
        return render_template('portfolio.html', **reports.detail(pid), missing_rates=[])

    @app.post('/goals/save')
    @validated
    def goal_save():
        commands.save_goal(Goal(f_str('name'), f_decimal('target_amount'), f_str('currency').upper(),
                                f_str('due_date'), f_str('note'), optional_id()))
        return redirect(url_for('settings')+'#goals')

    @app.post('/goals/<int:gid>/delete')
    @validated
    def goal_delete(gid):
        commands.delete_goal(gid)
        return redirect(url_for('settings')+'#goals')

    @app.post('/portfolios/save')
    @validated
    def portfolio_save():
        pid = commands.save_portfolio(Portfolio(f_str('name'), int(f_str('goal_id')), f_str('note'), optional_id()))
        return redirect(url_for('portfolio_detail', pid=pid))

    @app.post('/portfolios/<int:pid>/delete')
    @validated
    def portfolio_delete(pid):
        commands.delete_portfolio(pid)
        return redirect(url_for('portfolios'))

    @app.post('/portfolios/<int:pid>/allocate')
    @validated
    def portfolio_allocate(pid):
        kind, record_id = f_str('source').split(':')
        commands.allocate(pid, kind, int(record_id), f_str('percent').replace(',', '.'))
        return redirect(url_for('portfolio_detail', pid=pid))

    @app.post('/portfolios/<int:pid>/allocations/<int:rid>/delete')
    @validated
    def allocation_delete(pid, rid):
        commands.remove_allocation(pid, rid)
        return redirect(url_for('portfolio_detail', pid=pid))
