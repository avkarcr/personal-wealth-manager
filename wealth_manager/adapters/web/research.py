"""Inbound HTTP adapters; domain modules do not depend on Flask or SQLite."""
from flask import request, render_template, redirect, url_for, g, session, flash
from wealth_manager.shared.records import required
from wealth_manager.modules.companies.domain import Company
from wealth_manager.modules.macroeconomics.domain import Observation
from wealth_manager.modules.taxes.domain import TaxEntry, Deduction
from .commands import validated, optional_id
from .forms import f_str, f_decimal


def required_decimal(name):
    required(f_str(name))
    return f_decimal(name)


def register_research(app,runtime):
    services=runtime.services

    @app.get('/companies')
    def companies():
        return render_template('companies.html',**services['company_reports'].overview())

    @app.post('/companies/save')
    @validated
    def company_save():
        services['companies'].save(Company(name=f_str('name'),ticker=f_str('ticker').upper(),exchange=f_str('exchange').upper(),
            country=f_str('country').upper(),sector=f_str('sector'),website=f_str('website'),thesis=f_str('thesis'),status=f_str('status'),id=optional_id()))
        return redirect(url_for('companies'))

    @app.post('/companies/<int:rid>/delete')
    @validated
    def company_delete(rid):
        services['companies'].delete(rid)
        return redirect(url_for('companies'))

    @app.get('/macroeconomics')
    @validated
    def macroeconomics():
        return render_template('macroeconomics.html',**services['macro_reports'].overview(request.args.get('country') or 'RU'))

    @app.get('/macroeconomics/data')
    @validated
    def macro_data():
        from wealth_manager.modules.macroeconomics.data import LABELS
        return render_template('macro_data.html',**services['macro_data_reports'].overview(request.args.get('as_of') or None,request.args.get('mode') or 'known'),labels=LABELS)

    @app.get('/settings/macroeconomics')
    @validated
    def macro_settings():
        from datetime import date, timedelta
        from wealth_manager.modules.macroeconomics.scheduling import KINDS
        return render_template('macro_settings.html', state=services['macro_scheduler'].state(session.get('vault')),
                               kinds=KINDS, default_start=(date.today()-timedelta(days=30)).isoformat(),
                               today=date.today().isoformat())

    @app.post('/settings/macroeconomics')
    @validated
    def macro_settings_save():
        services['macro_scheduler'].configure(f_str('kind'), request.form.get('enabled')=='on',
            f_str('start'), int(f_str('hours')), f_str('access'), session.get('vault'))
        return redirect(url_for('macro_settings'))

    @app.post('/macroeconomics/refresh')
    @validated
    def macro_refresh():
        from .i18n import gettext
        token=session.get('vault')
        # The request hook owns this lock. Restore ownership before rendering/teardown.
        lock=g.wealth_vault_lock
        lock.release()
        try:
            services['macro_refresh'].execute(f_str('kind'),f_str('start'),f_str('end'),token)
        finally:
            lock.acquire()
            if not runtime.vault.unlocked or runtime.vault.token!=token:
                return redirect(url_for('unlock'))
        flash(gettext('Обновление завершено. Результат доступен в рядах данных и журнале'))
        return redirect(url_for('macro_data'))

    @app.post('/macroeconomics/save')
    @validated
    def macro_save():
        services['macroeconomics'].save(Observation(f_str('country').upper(),f_str('indicator'),f_str('date'),required_decimal('value'),f_str('source'),f_str('note'),optional_id()))
        return redirect(url_for('macroeconomics'))

    @app.post('/macroeconomics/<int:rid>/delete')
    @validated
    def macro_delete(rid):
        services['macroeconomics'].delete(rid)
        return redirect(url_for('macroeconomics'))

    @app.get('/taxes')
    @validated
    def taxes():
        return render_template('taxes.html',**services['tax_reports'].overview(request.args.get('year') or None,request.args.get('country') or None),missing_rates=[])

    @app.post('/taxes/entries/save')
    @validated
    def tax_entry_save():
        services['taxes'].save_entry(TaxEntry(int(f_str('year')),f_str('country').upper(),f_str('category'),f_str('currency').upper(),
            required_decimal('base'),required_decimal('rate'),f_decimal('paid'),f_str('note'),optional_id()))
        return redirect(url_for('taxes'))

    @app.post('/taxes/deductions/save')
    @validated
    def deduction_save():
        services['taxes'].save_deduction(Deduction(int(f_str('year')),f_str('country').upper(),f_str('category'),f_str('currency').upper(),
            required_decimal('amount'),f_decimal('refund'),f_str('status'),f_str('documents'),f_str('note'),optional_id(),
            actual_refund=f_decimal('actual_refund') if 'actual_refund' in request.form else None,received_date=f_str('received_date')))
        return redirect(url_for('taxes')+'#deductions')

    @app.post('/taxes/entries/<int:rid>/delete')
    @validated
    def tax_entry_delete(rid):
        services['taxes'].delete_entry(rid)
        return redirect(url_for('taxes'))

    @app.post('/taxes/deductions/<int:rid>/delete')
    @validated
    def deduction_delete(rid):
        services['taxes'].delete_deduction(rid)
        return redirect(url_for('taxes')+'#deductions')
