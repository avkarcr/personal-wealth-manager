from uuid import uuid4
from flask import render_template,redirect,url_for
from wealth_manager.modules.liabilities.domain import Liability, Payment, KINDS
from .commands import validated
from .forms import f_str, f_decimal


def register_liabilities(app,runtime):
    commands=runtime.services['liabilities']
    @app.get('/liabilities')
    def liabilities():
        return render_template('liabilities.html',**runtime.services['reports'].liabilities(),kinds=KINDS,request_id=uuid4().hex)
    @app.post('/liabilities/create')
    @validated
    def liability_create():
        commands.create(Liability(name=f_str('name'),opening_date=f_str('opening_date'),opening_principal=f_decimal('opening_principal'),
            currency=f_str('currency'),kind=f_str('kind'),asset_id=int(f_str('asset_id')) if f_str('asset_id') else None,
            creditor=f_str('creditor'),due_date=f_str('due_date'),note=f_str('note'),request_id=f_str('request_id')))
        return redirect(url_for('liabilities'))
    @app.post('/liabilities/pay')
    @validated
    def liability_pay():
        commands.pay(Payment(liability_id=int(f_str('liability_id')),date=f_str('date'),principal=f_decimal('principal'),
            interest=f_decimal('interest'),fee=f_decimal('fee'),cash_id=int(f_str('cash_id')) if f_str('cash_id') else None,
            note=f_str('note'),request_id=f_str('request_id')))
        return redirect(url_for('liabilities'))
    @app.post('/liabilities/<int:lid>/cancel')
    @validated
    def liability_cancel(lid):
        commands.cancel(lid);return redirect(url_for('liabilities'))
    @app.post('/liabilities/payments/<int:pid>/cancel')
    @validated
    def liability_payment_cancel(pid):
        commands.cancel_payment(pid);return redirect(url_for('liabilities'))
