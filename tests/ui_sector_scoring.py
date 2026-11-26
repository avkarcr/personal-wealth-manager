"""Synthetic M10 browser acceptance: input, saved evidence, settings and mobile."""
import os
import shutil
from pathlib import Path
from threading import Thread
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from test_sector_scoring import SectorScoringTests


def main():
    fixture=SectorScoringTests();fixture.setUp();server=None
    try:
        fixture.seed();app=fixture.app
        server=make_server('127.0.0.1',0,app,threaded=True);Thread(target=server.serve_forever,daemon=True).start()
        url=f'http://127.0.0.1:{server.server_port}'
        cookie=app.session_interface.get_signing_serializer(app).dumps({'vault':fixture.vault.token,'language':'ru'})
        screenshots=Path(os.environ.get('SECTOR_SCREENSHOTS','/tmp/m10-ui'));screenshots.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('MACRO_CHROMIUM_EXECUTABLE') or (shutil.which('chromium') if not os.environ.get('CI') else None),args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1440,'height':1000},color_scheme='light')
            context.add_cookies([dict(name='session',value=cookie,url=url,httpOnly=True,sameSite='Strict')])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url+'/macroeconomics/sectors')
            page.get_by_role('link',name='Информационные технологии',exact=True).click()
            assert page.locator('.sector-big-score').inner_text().strip()=='70 / 100'
            assert page.get_by_text('Недостаточно покрытия для итогового балла',exact=True).count()==0
            page.screenshot(path=str(screenshots/'sector-desktop-ru.png'),animations='disabled')
            summary=page.locator('#sector-observation-form > summary');summary.focus();page.keyboard.press('Enter')
            assert page.locator('#sector-observation-form').get_attribute('open') is not None
            form=page.locator('.sector-input-form')
            form.get_by_label('Показатель и единица',exact=True).select_option('liquidity')
            form.get_by_label('Значение',exact=True).fill('200')
            form.get_by_label('Начало периода',exact=True).fill('2026-11-01')
            form.get_by_label('Конец периода',exact=True).fill('2026-11-24')
            form.get_by_label('Дата публикации, если известна',exact=True).fill('2026-11-25')
            form.get_by_label('Источник',exact=True).fill('Synthetic UI liquidity')
            form.get_by_label('Методика, состав, период отчётности и ориентир',exact=True).fill('Synthetic universe; average daily turnover, 20 sessions; 10 distinct issuers')
            form.get_by_label('Эмитентов с данными',exact=True).fill('10')
            form.get_by_label('Всего эмитентов в совокупности',exact=True).fill('10')
            form.get_by_role('button',name='Сохранить показатель',exact=True).click()
            assert page.get_by_text('Synthetic UI liquidity',exact=True).count()==1
            page.get_by_role('button',name='Сохранить расчёт с исходными версиями',exact=True).click()
            assert '/runs/' in page.url
            page.get_by_role('link',name='Информационные технологии',exact=True).click()
            assert page.get_by_text('Добавить показатель или исправление',exact=True).count()==0
            assert page.locator('.sector-big-score').inner_text().strip()=='70 / 100'
            fixture.s['change_language'].execute('en');page.reload();page.locator('#theme-toggle').click()
            page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.get_by_role('heading',name='Information technology',exact=True).count()==1
            page.screenshot(path=str(screenshots/'sector-mobile-en-dark.png'),animations='disabled')
            page.goto(url+'/settings/sector-scoring')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.get_by_label('Minimum daily turnover, RUB million',exact=True).fill('250')
            page.get_by_role('button',name='Save and recalculate',exact=True).click()
            page.get_by_role('link',name='Information technology',exact=True).click()
            assert page.get_by_text('Insufficient liquidity',exact=True).count()==1
            page.goto(url+f'/settings/sector-scoring/{fixture.sid}')
            page.get_by_label('Applicability profile',exact=True).select_option('gold')
            page.get_by_role('button',name='Apply profile defaults',exact=True).click()
            assert page.get_by_label('Applicability profile',exact=True).input_value()=='gold'
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(screenshots/'sector-settings-mobile-en-dark.png'),animations='disabled')
            page.goto(url+'/macroeconomics/dashboard?as_of=2026-11-26')
            assert page.locator('#sector-ranking').count()==1
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            context.close();browser.close()
        print('Sector Chromium OK: dated input, frozen results, liquidity gate, profiles, keyboard, RU/EN, 1440/390px and light/dark; no JS errors.')
    finally:
        if server:server.shutdown();server.server_close()
        fixture.doCleanups()


if __name__=='__main__':main()
