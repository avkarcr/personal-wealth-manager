from flask import render_template,redirect,url_for,session
from wealth_manager.modules.instruments.domain import CryptoAsset
from wealth_manager.modules.market_catalog.iss import ACCESS_MODES
from .forms import f_str
from .commands import validated,optional_id


def register_instruments(app,runtime):
    commands=runtime.services['instruments'];reports=runtime.services['instrument_reports']
    @app.get('/crypto-assets')
    def crypto_assets():return render_template('crypto_assets.html',**reports.overview())
    @app.get('/companies/<int:rid>')
    @validated
    def company_detail(rid):return render_template('instrument.html',**reports.detail('company',rid))
    @app.get('/securities/<int:rid>')
    @validated
    def security_detail(rid):
        detail = reports.detail('security', rid)
        access = runtime.services['macro_scheduler'].state(session.get('vault'))['access']
        return render_template('instrument.html', **detail, iss_access=access in ACCESS_MODES)
    @app.get('/crypto-assets/<int:rid>')
    @validated
    def crypto_detail(rid):return render_template('instrument.html',**reports.detail('crypto',rid))
    @app.post('/crypto-assets/save')
    @validated
    def crypto_save():
        rid=commands.save_crypto(CryptoAsset(name=f_str('name'),symbol=f_str('symbol').upper(),network=f_str('network'),contract=f_str('contract'),
            website=f_str('website'),note=f_str('note'),archived=f_str('archived')=='1',id=optional_id()))
        return redirect(url_for('crypto_detail',rid=rid))
    @app.post('/crypto-assets/<int:rid>/delete')
    @validated
    def crypto_delete(rid):
        commands.delete_crypto(rid);return redirect(url_for('crypto_assets'))
    @app.post('/instruments/<kind>/<int:rid>/link')
    @validated
    def instrument_link(kind,rid):
        commands.link(int(f_str('holding_id')),kind,rid,int(f_str('security_id')) if f_str('security_id') else None)
        return redirect(url_for('company_detail' if kind=='company' else 'security_detail' if kind=='security' else 'crypto_detail',rid=rid))
    @app.post('/positions/<int:hid>/unlink')
    @validated
    def instrument_unlink(hid):
        previous=commands.unlink(hid)
        if previous:
            kind='company' if previous['company_id'] is not None else 'crypto'
            return redirect(url_for('company_detail' if kind=='company' else 'crypto_detail',rid=previous[kind+'_id']))
        return redirect(url_for('companies'))
