"""Chromium acceptance on an isolated synthetic vault; never loads investor data."""
from datetime import datetime, time, timezone
import os
from pathlib import Path
import shutil
from threading import Thread
from unittest.mock import Mock
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
import test_market_catalog as fixtures
from test_company_explorer import ISIN, CANDLE_FIELDS, table, reference, candle
from wealth_manager.adapters.market_data.iss import IssProvider
from wealth_manager.modules.companies.domain import Company
from wealth_manager.modules.market_catalog.domain import Listing,Sector,Security


def main():
    fixture=fixtures.CatalogTests();fixture.setUp();server=None
    try:
        cid=fixture.s['companies'].save(Company('Демонстрационный эмитент', status='owned'))
        sid=fixture.command().save_security(Security(cid,'Демо — обыкновенные акции','common_share',isin=ISIN,currency='RUB'));fixture.command().save_security(Security(cid,'Демо — привилегированные акции','preferred_share'));fixture.command().save_security(Security(cid,'Демо — облигация 001','bond'))
        sector=fixture.command().save_sector(Sector('demo_banks','Банковские услуги','Banking services',fixture.sector('finance')['id']))
        lid=fixture.command().save_listing(Listing(sid,'MOEX','DEMO_OLD','TQBR',fixture.days(60),status='active',as_of=fixture.days(20),source='Synthetic exchange notice'))
        fixture.command().rename_listing(lid,'DEMO',fixture.days(5),'Synthetic ticker notice')
        fixture.s['instruments'].link(fixture.hid,'company',cid)
        fixture.s['planning'].allocate(fixture.pid,'holding',fixture.hid,40)
        fixture.issuer('Другая синтетическая компания')
        sync=fixture.app.extensions['wealth_manager'].services['listing_iss']
        transport=Mock()
        transport.get.side_effect=[
            reference('DEMO'),
            table('candles',CANDLE_FIELDS,[candle(fixture.days(1),105,0),candle(fixture.days(3),100,200)]),
        ]
        sync.provider=IssProvider(transport)
        sync.clock=lambda:datetime.combine(fixture.today,time(12),tzinfo=timezone.utc)
        app=fixture.app;server=make_server('127.0.0.1',0,app,threaded=True);Thread(target=server.serve_forever,daemon=True).start()
        url=f'http://127.0.0.1:{server.server_port}'
        cookie=app.session_interface.get_signing_serializer(app).dumps({'vault':fixture.vault.token,'language':'ru'})
        screenshots=Path(os.environ.get('CATALOG_SCREENSHOTS','/tmp/m9-ui'));screenshots.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('MACRO_CHROMIUM_EXECUTABLE') or (shutil.which('chromium') if not os.environ.get('CI') else None),args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1440,'height':1000},color_scheme='light')
            context.add_cookies([dict(name='session',value=cookie,url=url,httpOnly=True,sameSite='Strict')])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url+f'/companies/{cid}')
            page.get_by_text('Назначить отрасль или подотрасль',exact=True).click()
            form=page.locator(f'form[action="/companies/{cid}/sectors"]')
            form.get_by_label('Отрасль или подотрасль',exact=True).select_option(str(sector))
            form.get_by_label('Основное направление',exact=True).check()
            form.get_by_role('button',name='Сохранить',exact=True).click()
            assert page.get_by_text('Финансовый сектор / Банковские услуги',exact=True).count()>=1
            page.get_by_label('Уточнить выпуск позиции',exact=True).select_option(str(sid))
            page.get_by_role('button',name='Привязать выпуск',exact=True).click()
            assert page.get_by_text('Привязан только эмитент; конкретный выпуск не выбран',exact=True).count()==0
            page.evaluate('scrollTo(0,0)');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(animations='disabled',path=str(screenshots/'issuer-desktop.png'))

            finance=fixture.sector('finance')['id']
            page.goto(url+f'/macroeconomics/sectors/{finance}')
            page.get_by_role('link',name='Текущие компании отрасли').click()
            assert page.get_by_label('Отрасль или подотрасль',exact=True).input_value()==str(finance)
            page.get_by_label('Название, тикер, ISIN или регномер',exact=True).fill('demo_old')
            page.get_by_label('Название, тикер, ISIN или регномер',exact=True).press('Enter')
            issuer=page.locator(f'#company-{cid}')
            assert issuer.count()==1
            assert issuer.get_by_text('DEMO_OLD',exact=True).count()==1
            assert issuer.locator(f'a[href="/portfolios/{fixture.pid}"]').count()==1
            assert page.get_by_role('heading',name='Другая синтетическая компания').count()==0
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(animations='disabled',path=str(screenshots/'explorer-desktop.png'))
            issuer.get_by_role('button',name='Добавить в наблюдение').click()
            assert page.locator('.card-head .tag').get_by_text('Наблюдаю',exact=True).count()==1

            page.goto(url+f'/securities/{sid}');page.locator('#security-listings').scroll_into_view_if_needed()
            assert page.get_by_role('heading',name='DEMO · MOEX / TQBR',exact=True).count()==1
            assert page.get_by_text('Завершённый листинг',exact=True).count()==1
            assert page.get_by_role('link',name='Выберите режим доступа MOEX').count()==1
            fixture.s['macro_scheduler'].configure('key_rate',False,fixture.days(7),24,'familiarization',fixture.vault.token)
            page.reload()
            page.get_by_role('button',name='Обновить из MOEX ISS').click()
            assert page.get_by_text('Сведения ISS обновлены',exact=True).count()==1
            assert page.get_by_text('Дневной оборот 0,00',exact=False).count()==1
            assert transport.get.call_count==2
            first=page.locator('.catalog-listing').first;first.get_by_text('Изменить статус или сроки',exact=True).click()
            edit=first.locator(f'form[action="/securities/{sid}/listings"]')
            edit.get_by_label('Статус торгов',exact=True).select_option('suspended')
            edit.get_by_label('Дата подтверждения',exact=True).fill(fixture.day)
            edit.get_by_label('Источник сведений',exact=True).fill('Synthetic suspension notice')
            edit.get_by_role('button',name='Сохранить листинг',exact=True).click()
            assert page.locator('.catalog-status.status-suspended').count()==1
            page.locator('#security-listings').scroll_into_view_if_needed();page.screenshot(animations='disabled',path=str(screenshots/'listings-desktop.png'))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#security-listings').scroll_into_view_if_needed();page.screenshot(animations='disabled',path=str(screenshots/'listings-mobile.png'))
            fixture.s['change_language'].execute('en');page.reload();page.locator('#theme-toggle').click()
            assert page.get_by_text('Trading suspended',exact=True).count()>=1
            page.locator('#security-listings').scroll_into_view_if_needed();page.screenshot(animations='disabled',path=str(screenshots/'listings-mobile-en-dark.png'))
            page.goto(url+'/companies/sectors');assert page.get_by_role('heading',name='Financials / Banking services',exact=True).count()==1
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(animations='disabled',path=str(screenshots/'industries-mobile-en.png'))
            page.goto(url+f'/companies?sector={sector}&order=turnover')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.get_by_label('Sort by',exact=True).input_value()=='turnover'
            assert page.get_by_text('Comparable TQBR turnover data is unavailable',exact=False).count()==1
            assert page.get_by_text('Trading suspended',exact=False).count()>=1
            page.screenshot(animations='disabled',path=str(screenshots/'explorer-mobile-en-dark.png'))
            assert not errors,errors
            context.close();browser.close()
        print('Catalog/Explorer Chromium OK: classification, issue binding, history, keyboard search, watchlist, portfolios, synthetic ISS, RU/EN, 1440/390px, light/dark.')
    finally:
        if server:server.shutdown();server.server_close()
        fixture.doCleanups()


if __name__=='__main__':main()
