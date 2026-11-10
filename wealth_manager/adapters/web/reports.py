"""Read-only report endpoints; no database access or snapshot writes."""
from flask import render_template, request
from .commands import validated


def register_reports(app,runtime):
    reports=runtime.services['reports']
    @app.get('/')
    def dashboard(): return render_template('dashboard.html',**reports.dashboard())

    @app.get('/accounts/<int:aid>')
    @validated
    def account(aid):
        template,context=reports.account(aid)
        return render_template(template,**context)

    @app.get('/operations')
    def operations():
        return render_template('operations.html',**reports.operations(**{k:request.args.get(k) for k in ('account','type','year')}))

    @app.get('/income')
    def income():return render_template('income.html',**reports.income(request.args.get('year') or None))

    @app.get('/settings')
    def settings():
        return render_template('settings.html',**reports.settings(),db_path=runtime.vault.path,idle=runtime.vault.idle_minutes,
                               market=runtime.services['market_data'].preferences(request.args.get('country')))
