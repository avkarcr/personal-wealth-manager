"""Inbound HTTP adapters; domain modules do not depend on Flask or SQLite."""
from flask import render_template, redirect, url_for
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
    def macroeconomics():
        return render_template('macroeconomics.html',**services['macro_reports'].overview())

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
    def taxes():
        return render_template('taxes.html',**services['tax_reports'].overview(),missing_rates=[])

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
            required_decimal('amount'),f_decimal('refund'),f_str('status'),f_str('documents'),f_str('note'),optional_id()))
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
