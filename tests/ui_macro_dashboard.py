"""Optional real Chromium smoke check; uses only an isolated synthetic vault.

Run: PYTHONPATH=tests:. python tests/ui_macro_dashboard.py
Needs playwright and Chromium (MACRO_CHROMIUM_EXECUTABLE can override its path).
Screenshots are temporary diagnostics, never user data or repository fixtures.
"""
import os
from pathlib import Path
import shutil
from threading import Thread
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
import test_economic_cycle as fixtures
from wealth_manager.factory import create_app


def main():
    fixture=fixtures.CycleTests();fixture.setUp()
    server=None
    try:
        fixture.seed()
        app=create_app({'TESTING':True},vault=fixture.vault)
        app.extensions['wealth_manager'].services['macro_dashboard'].clock=lambda:'2026-11-22T12:00:00+00:00'
        server=make_server('127.0.0.1',0,app,threaded=True);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        cookie=app.session_interface.get_signing_serializer(app).dumps({'vault':fixture.vault.token,'language':'ru'})
        screenshots=Path(os.environ.get('MACRO_SCREENSHOTS','/tmp/m8-ui'));screenshots.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('MACRO_CHROMIUM_EXECUTABLE') or (shutil.which('chromium') if not os.environ.get('CI') else None),args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1440,'height':1000},color_scheme='light')
            context.add_cookies([dict(name='session',value=cookie,url=url,httpOnly=True,sameSite='Strict')])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url+'/macroeconomics/dashboard?window=5&chart=gdp');page.locator('#macro-plot svg path').first.wait_for()
            assert page.get_by_role('heading',name='Экономика и инвестиционный цикл').count()==1
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(animations='disabled',path=str(screenshots/'desktop-light.png'))
            plot=page.locator('#macro-plot');plot.focus();plot.press('Home');first=page.locator('#macro-readout').inner_text();plot.press('End');last=page.locator('#macro-readout').inner_text();assert first!=last
            light=page.locator('#macro-plot svg path').first.evaluate('(e)=>getComputedStyle(e).stroke')
            page.locator('#theme-toggle').click()
            dark=page.locator('#macro-plot svg path').first.evaluate('(e)=>getComputedStyle(e).stroke');assert light!=dark
            page.locator('#macro-history').scroll_into_view_if_needed();page.screenshot(animations='disabled',path=str(screenshots/'chart-dark.png'))
            page.set_viewport_size({'width':390,'height':844});page.locator('#theme-toggle').click();page.evaluate('scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(animations='disabled',path=str(screenshots/'mobile-light.png'))
            page.get_by_role('link',name='Кривая ОФЗ',exact=True).click();page.locator('#macro-plot svg circle').first.wait_for()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#macro-history').scroll_into_view_if_needed();page.screenshot(animations='disabled',path=str(screenshots/'mobile-curve.png'))
            page.get_by_role('link',name='История фаз цикла',exact=True).click();page.locator('#macro-plot svg rect').first.wait_for()
            plot.focus();plot.press('Home');assert 'Индекс уверенности' in page.locator('#macro-readout').inner_text()
            fixture.s['change_language'].execute('en');page.reload();assert page.get_by_role('heading',name='Economy and investment cycle').count()==1
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#macro-history').scroll_into_view_if_needed();page.screenshot(animations='disabled',path=str(screenshots/'mobile-phases-en.png'))
            assert not errors,errors
            context.close()
            nojs=browser.new_context(java_script_enabled=False,viewport={'width':390,'height':844})
            nojs.add_cookies([dict(name='session',value=cookie,url=url,httpOnly=True,sameSite='Strict')])
            page=nojs.new_page();page.goto(url+'/macroeconomics/dashboard?chart=cpi')
            page.get_by_text('Exact values and observation revisions',exact=False).click();assert page.locator('.macro-details table tbody tr').count()>0
            nojs.close();browser.close()
        print('Chromium OK: RU/EN, 1440/390px, light/dark, keyboard, curve, phase history, no-JS table; no JS errors.')
        print('Screenshots:',screenshots)
    finally:
        if server:server.shutdown();server.server_close()
        fixture.doCleanups()


if __name__=='__main__':main()
