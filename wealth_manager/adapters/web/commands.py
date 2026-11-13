"""Inbound adapters: parse forms, call application commands, render responses."""
from datetime import date
from decimal import InvalidOperation
from functools import wraps
from flask import abort, flash, redirect, request, url_for, render_template
from wealth_manager.modules.planning.domain import PlanningError
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.classifications import CATEGORY_ALIASES

from wealth_manager.shared.errors import EntityNotFound, RentalFlowProtected
from wealth_manager.modules.accounts.domain import Purchase
from wealth_manager.modules.entities.domain import RecordDraft as Account
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.rentals.domain import Lease, RentPayment
from .i18n import gettext
from .forms import f_str, f_float, f_decimal, back


def validated(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except EntityNotFound:
            abort(404)
        except PlanningError as error:
            return render_template('planning_error.html', error=str(error), missing_rates=[]), 400
        except RecordError as error:
            return render_template('record_error.html', error=str(error)), 400
        except (ValueError, InvalidOperation):
            abort(400)
    return wrapped


def optional_id():
    value = request.form.get('id')
    return int(value) if value else None


def rent_redirect(account_id):
    return redirect(url_for('account', aid=account_id) + '#rent')


def register_commands(app, runtime):
    services = runtime.services

    @app.post('/accounts/save')
    @validated
    def account_save():
        account = Account(id=optional_id(), name=f_str('name') or 'Без названия', kind=f_str('kind', 'broker'),
                          institution=f_str('institution'), currency=f_str('currency', 'RUB'),
                          current_value=f_decimal('current_value'), tax_rate=f_decimal('tax_rate'), note=f_str('note'))
        purchase = None
        if account.id is None and account.kind == 'realty':
            purchase = Purchase(f_decimal('purchase_price'), date.fromisoformat(f_str('purchase_date') or date.today().isoformat()))
        aid = services['save_account'].execute(account, purchase)
        return redirect(url_for('account', aid=aid))

    @app.post('/accounts/<int:aid>/value')
    @validated
    def account_value(aid):
        services['accounts'].set_value(aid, f_float('current_value'))
        return redirect(url_for('account', aid=aid))

    @app.post('/accounts/<int:aid>/archive')
    @validated
    def account_archive(aid):
        services['accounts'].toggle_archived(aid)
        flash('Статус счёта изменён')
        return back()

    @app.post('/accounts/<int:aid>/close')
    @validated
    def account_close(aid):
        services['accounts'].close(aid)
        return redirect(url_for('account',aid=aid))

    @app.post('/accounts/<int:aid>/reopen')
    @validated
    def account_reopen(aid):
        services['accounts'].reopen(aid)
        return redirect(url_for('account',aid=aid))

    @app.post('/accounts/<int:aid>/delete')
    @validated
    def account_delete(aid):
        services['accounts'].delete(aid)
        flash('Счёт удалён')
        return redirect(url_for('dashboard'))

    @app.post('/accounts/<int:aid>/history')
    @validated
    def account_history_point(aid):
        services['accounts'].record_history(aid, f_str('date') or date.today().isoformat(), f_float('value'), f_str('currency'))
        flash('Точка истории добавлена')
        return redirect(url_for('account', aid=aid))

    @app.post('/holdings/save')
    @validated
    def holding_save():
        position = Position(id=optional_id(), account_id=int(f_str('account_id')),
                            name=f_str('name'), ticker=f_str('ticker'), asset_class=f_str('asset_class') if request.form.get('asset_class_code') in (None,'custom') else '', asset_class_code=f_str('asset_class_code','stocks'),
                            currency=f_str('currency', 'RUB'), quantity=f_float('quantity'),
                            avg_price=f_float('avg_price'), price=f_float('price'))
        services['positions'].save(position)
        return back()

    @app.post('/holdings/<int:hid>/delete')
    @validated
    def holding_delete(hid):
        services['positions'].delete(hid)
        return back()

    @app.post('/accounts/<int:aid>/prices')
    @validated
    def account_prices(aid):
        prices = {}
        for key, value in request.form.items():
            if key.startswith('price_'):
                try:
                    price = float(value.replace(',', '.').replace(' ', '').replace(' ', ''))
                except ValueError:
                    continue
                prices[int(key[6:])] = price
        services['positions'].update_prices(aid, prices)
        flash('Цены обновлены')
        return redirect(url_for('account', aid=aid))

    @app.post('/flows/save')
    @validated
    def flow_save():
        flow = Cashflow(id=optional_id(), account_id=int(f_str('account_id')),
                        date=f_str('date') or date.today().isoformat(), type=f_str('type', 'deposit'),
                        amount=f_float('amount'), currency=f_str('currency', 'RUB'),
                        category=f_str('category'), category_code=f_str('category_code') if 'category_code' in request.form else CATEGORY_ALIASES.get(f_str('category'),''), source=f_str('source'),
                        pending=bool(request.form.get('pending')), note=f_str('note'))
        try:
            services['cashflows'].save(flow)
        except RentalFlowProtected:
            flash('Эта операция создана из платежа по аренде — измените сам платёж на странице объекта')
        return back()

    @app.post('/flows/<int:fid>/paid')
    @validated
    def flow_paid(fid):
        try:
            services['cashflows'].mark_received(fid)
        except RentalFlowProtected as error:
            flash('Отметьте получение денег от риелтора в платеже — там же укажите сумму и курс')
            return rent_redirect(error.account_id)
        flash('Отмечено как полученное')
        return back()

    @app.post('/flows/<int:fid>/delete')
    @validated
    def flow_delete(fid):
        try:
            services['cashflows'].delete(fid)
        except RentalFlowProtected:
            flash('Эта операция создана из платежа по аренде — удалите сам платёж на странице объекта')
        return back()

    @app.post('/leases/save')
    @validated
    def lease_save():
        try:
            pay_day = max(0, min(31, int(f_str('pay_day') or 0)))
        except ValueError:
            pay_day = 0
        lease = Lease(id=optional_id(), asset_id=int(f_str('account_id')), tenant=f_str('tenant') or 'Арендатор',
                      contact=f_str('contact'), start_date=f_str('start_date') or date.today().isoformat(),
                      end_date=f_str('end_date'), rent_amount=f_float('rent_amount'), currency=f_str('currency', 'RUB'),
                      frequency=f_str('frequency', 'month'), pay_day=pay_day, deposit_amount=f_float('deposit_amount'),
                      deposit_currency=f_str('deposit_currency', 'RUB'), agent=f_str('agent'), agent_fee=f_str('agent_fee'),
                      utilities=f_str('utilities'), terms=f_str('terms'))
        _, count = services['rentals'].save_lease(lease, bool(request.form.get('schedule')))
        flash(gettext('Договор сохранён, добавлено платежей в график: {count}').format(count=count) if count else gettext('Договор сохранён'))
        return rent_redirect(lease.asset_id)

    @app.post('/leases/<int:lid>/schedule')
    @validated
    def lease_schedule(lid):
        aid, count = services['rentals'].generate_schedule(lid)
        flash(gettext('Добавлено платежей в график: {count}').format(count=count) if count else gettext('График уже актуален'))
        return rent_redirect(aid)

    @app.post('/leases/<int:lid>/delete')
    @validated
    def lease_delete(lid):
        aid = services['rentals'].delete_lease(lid)
        flash('Договор и его платежи удалены')
        return rent_redirect(aid)

    @app.post('/rent/save')
    @validated
    def rent_save():
        payment = RentPayment(id=optional_id(), lease_id=int(f_str('lease_id')),
                              period_start=f_str('period_start') or date.today().isoformat(), period_end=f_str('period_end'),
                              due_date=f_str('due_date'), amount=f_float('amount'), currency=f_str('currency'),
                              status=f_str('status', 'expected'), tenant_paid_date=f_str('tenant_paid_date'),
                              received_date=f_str('received_date'), received_amount=f_float('received_amount'),
                              received_currency=f_str('received_currency', 'RUB'), cb_rate=f_float('cb_rate'), note=f_str('note'))
        _, aid = services['rentals'].save_payment(payment)
        return rent_redirect(aid)

    @app.post('/rent/<int:pid>/delete')
    @validated
    def rent_delete(pid):
        return rent_redirect(services['rentals'].delete_payment(pid))
