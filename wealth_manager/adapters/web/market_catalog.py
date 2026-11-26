"""HTTP forms for the existing company cards and their issues/listings."""
from flask import flash, g, render_template, redirect, session, url_for
from wealth_manager.modules.market_catalog.domain import Sector,Security,Listing
from wealth_manager.shared.records import RecordError
from .forms import f_str
from .commands import validated,optional_id


def nullable_id(name):return int(f_str(name)) if f_str(name) else None


def register_catalog(app,runtime):
    commands=runtime.services['market_catalog'];reports=runtime.services['catalog_reports']
    @app.get('/companies/sectors')
    @validated
    def sectors():return render_template('sectors.html',**reports.overview())
    @app.post('/companies/sectors/save')
    @validated
    def sector_save():
        commands.save_sector(Sector(f_str('code'),f_str('name'),f_str('name_en'),nullable_id('parent_id'),f_str('source_url'),f_str('archived')=='1',optional_id()))
        return redirect(url_for('sectors'))
    @app.post('/companies/sectors/<int:rid>/delete')
    @validated
    def sector_delete(rid):
        commands.delete_sector(rid);return redirect(url_for('sectors'))
    @app.post('/companies/<int:cid>/sectors')
    @validated
    def company_sector_save(cid):
        commands.assign_sector(cid,int(f_str('sector_id')),f_str('is_primary')=='1',f_str('source'),f_str('as_of'))
        return redirect(url_for('company_detail',rid=cid)+'#company-sectors')
    @app.post('/companies/<int:cid>/sectors/<int:sid>/delete')
    @validated
    def company_sector_delete(cid,sid):
        commands.remove_sector(cid,sid);return redirect(url_for('company_detail',rid=cid)+'#company-sectors')
    @app.post('/companies/<int:cid>/securities')
    @validated
    def security_save(cid):
        sid=commands.save_security(Security(company_id=cid,name=f_str('name'),kind=f_str('kind'),isin=f_str('isin').upper(),registration_number=f_str('registration_number'),
                currency=f_str('currency').upper(),underlying_id=nullable_id('underlying_id'),archived=f_str('archived')=='1',source=f_str('source'),as_of=f_str('as_of'),id=optional_id()))
        return redirect(url_for('security_detail',rid=sid))
    @app.post('/securities/<int:rid>/delete')
    @validated
    def security_delete(rid):
        cid=reports.security(rid)['company_id'];commands.delete_security(rid)
        return redirect(url_for('company_detail',rid=cid))
    @app.post('/securities/<int:sid>/listings')
    @validated
    def listing_save(sid):
        commands.save_listing(Listing(sid,f_str('exchange').upper(),f_str('ticker').upper(),f_str('board').upper(),f_str('valid_from'),f_str('valid_to'),f_str('status'),f_str('as_of'),f_str('source'),optional_id()))
        return redirect(url_for('security_detail',rid=sid)+'#security-listings')
    @app.post('/securities/listings/<int:rid>/rename')
    @validated
    def listing_rename(rid):
        sid=reports.listing(rid)['security_id'];commands.rename_listing(rid,f_str('ticker').upper(),f_str('effective_date'),f_str('source'))
        return redirect(url_for('security_detail',rid=sid)+'#security-listings')
    @app.post('/securities/listings/<int:rid>/iss')
    @validated
    def listing_iss_refresh(rid):
        sid = reports.listing(rid)['security_id']
        token = session.get('vault')
        lock = g.wealth_vault_lock
        lock.release()
        try:
            runtime.services['listing_iss'].execute(rid, token)
            message = 'Сведения ISS обновлены'
        except RecordError as error:
            message = str(error)
        except OSError:
            message = 'Не удалось сохранить сведения ISS'
        finally:
            lock.acquire()
        if not runtime.vault.unlocked or runtime.vault.token != token or runtime.vault.idle_expired():
            return redirect(url_for('unlock'))
        flash(message)
        return redirect(url_for('security_detail', rid=sid, _anchor=f'listing-{rid}'))

    @app.post('/securities/listings/<int:rid>/delete')
    @validated
    def listing_delete(rid):
        sid=reports.listing(rid)['security_id'];commands.delete_listing(rid)
        return redirect(url_for('security_detail',rid=sid)+'#security-listings')
