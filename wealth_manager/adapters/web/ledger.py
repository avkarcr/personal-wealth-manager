from uuid import uuid4
from flask import render_template, redirect, url_for
from .commands import validated
from .forms import f_str, f_decimal
from wealth_manager.modules.ledger.domain import Entry, KINDS


def register_ledger(app,runtime):
    commands=runtime.services['ledger']
    @app.get('/ledger')
    def ledger():
        return render_template('ledger.html',ledger=runtime.services['ledger_reports'].overview(),kinds=KINDS,request_id=uuid4().hex)
    @app.post('/ledger/activate/<int:aid>')
    @validated
    def ledger_activate(aid):
        commands.activate(aid)
        return redirect(url_for('ledger'))
    @app.post('/ledger/post')
    @validated
    def ledger_post():
        commands.post(Entry(date=f_str('date'),kind=f_str('kind'),holding_id=int(f_str('holding_id')),
                            quantity=f_decimal('quantity'),price=f_decimal('price'),fee=f_decimal('fee'),
                            cash_id=int(f_str('cash_id')) if f_str('cash_id') else None,
                            target_id=int(f_str('target_id')) if f_str('target_id') else None,
                            note=f_str('note'),request_id=f_str('request_id')))
        return redirect(url_for('ledger'))
    @app.post('/ledger/<int:eid>/cancel')
    @validated
    def ledger_cancel(eid):
        commands.cancel(eid)
        return redirect(url_for('ledger'))
