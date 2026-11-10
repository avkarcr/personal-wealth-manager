"""Explicit entry points for assets and income sources; old account URLs remain compatible."""
from datetime import date
from flask import request, render_template, redirect, url_for, abort
from wealth_manager.modules.assets.domain import Asset
from wealth_manager.modules.income_sources.domain import IncomeSource
from wealth_manager.modules.accounts.domain import Purchase
from .forms import f_str, f_decimal
from .commands import validated, optional_id


def register_entities(app,runtime):
    services=runtime.services
    def common():
        return dict(id=optional_id(),name=f_str('name'),currency=f_str('currency','RUB'),institution=f_str('institution'),note=f_str('note'))

    @app.post('/assets/save')
    @validated
    def asset_save():
        asset=Asset(current_value=f_decimal('current_value'),**common())
        purchase=Purchase(f_decimal('purchase_price'),date.fromisoformat(f_str('purchase_date') or date.today().isoformat())) if asset.id is None else None
        aid=services['save_asset'].execute(asset,purchase)
        return redirect(url_for('asset',aid=aid))

    @app.post('/income-sources/save')
    @validated
    def income_source_save():
        source=IncomeSource(tax_rate=f_decimal('tax_rate'),**common())
        aid=services['save_income_source'].execute(source)
        return redirect(url_for('income_source',aid=aid))

    def show(aid,kind):
        template,context=services['reports'].account(aid)
        if context['acc']['kind']!=kind:abort(404)
        return render_template(template,**context)

    @app.get('/assets/<int:aid>')
    @validated
    def asset(aid):return show(aid,'realty')

    @app.get('/income-sources/<int:aid>')
    @validated
    def income_source(aid):return show(aid,'freelance')

    @app.context_processor
    def urls():
        def entity_url(entity):
            endpoint={'realty':'asset','freelance':'income_source'}.get(entity['kind'],'account')
            return url_for(endpoint,aid=entity['id'])
        return dict(entity_url=entity_url)
