"""Inbound market adapters: forms and localized outcomes only."""
from functools import wraps
from flask import request, redirect, url_for, flash
from wealth_manager.modules.market_data.domain import ProviderError, MissingRate, NoExchangeSelected
from wealth_manager.shared.errors import EntityNotFound
from .commands import validated
from .i18n import gettext as _
from .forms import f_str, f_float


def register_market_data(app,runtime):
    market=runtime.services['market_data']

    def feedback(handler):
        @wraps(handler)
        def wrapped(*args,**kwargs):
            try: result=handler(*args,**kwargs)
            except NoExchangeSelected:
                flash(_('Выберите страну и источники криптокотировок в настройках'))
            except MissingRate:
                flash(_('Добавьте необходимые курсы валют в настройках'))
            except ProviderError:
                flash(_('Источник котировок временно недоступен'))
            else:
                if result is not None:
                    flash(_('Обновлено: {count}').format(count=result['count']))
                    if result.get('failed'):flash(_('Нет котировок: {symbols}').format(symbols=', '.join(result['failed'])))
                    if result.get('sources'):flash(_('Источники: {sources}').format(sources=', '.join(sorted(set(result['sources'].values())))))
            return redirect(url_for('account',aid=kwargs['aid']) if 'aid' in kwargs else url_for('settings'))
        return wrapped

    @app.post('/settings/market')
    @validated
    def market_settings_save():
        market.save_preferences(f_str('country'),request.form.getlist('exchanges'))
        flash(_('Сохранено'))
        return redirect(url_for('settings',_anchor='market'))

    @app.post('/accounts/<int:aid>/crypto')
    @validated
    @feedback
    def account_crypto(aid): return market.refresh_account(aid,'crypto')

    @app.post('/accounts/<int:aid>/moex')
    @validated
    @feedback
    def account_moex(aid): return market.refresh_account(aid,'moex')

    @app.post('/rates/cbr')
    @validated
    @feedback
    def rates_cbr(): return market.refresh_cbr()

    @app.post('/rates/crypto')
    @validated
    @feedback
    def rates_crypto(): return market.refresh_crypto_rates()

    @app.post('/rates/save')
    @validated
    def rates_save():
        values={}
        for key,value in request.form.items():
            if key.startswith('rate_'):
                try: values[key[5:]]=float(value.replace(',','.').replace(' ',''))
                except ValueError: continue
        if f_str('new_currency'):values[f_str('new_currency').upper()]=f_float('new_rate')
        market.save_rates(values,f_str('base_currency'))
        flash(_('Сохранено'))
        return redirect(url_for('settings'))

    @app.post('/rates/<cur>/delete')
    def rate_delete(cur):
        market.delete_rate(cur)
        return redirect(url_for('settings'))

    @app.get('/api/cbr')
    def api_cbr():
        try:return market.historical_rate((request.args.get('cur') or 'USD').upper(),request.args.get('date') or None)
        except EntityNotFound:return {'error':_('У ЦБ нет курса выбранной валюты')},404
        except ValueError:return {'error':_('Некорректная дата')},400
        except ProviderError:return {'error':_('Источник котировок временно недоступен')},502
