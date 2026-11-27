"""First-run wealth workflow in Chromium, using a new synthetic vault."""
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from wealth_manager.factory import create_app


def check_account(page, url):
    page.goto(url)
    page.get_by_label('Пароль (минимум 8 символов)').fill('synthetic-wealth')
    page.get_by_label('Повторите пароль').fill('synthetic-wealth')
    page.get_by_role('button', name='Создать хранилище').click()

    page.get_by_role('button', name='Добавить первый счёт').click()
    account = page.locator('#dlg-account')
    account.get_by_label('Название', exact=True).fill('Тестовый брокер')
    account.get_by_role('combobox', name=re.compile('Валюта')).select_option('RUB')
    account.get_by_role('button', name='Сохранить', exact=True).click()
    expect(page.get_by_role('heading', name='Тестовый брокер')).to_be_visible()
    account_url = page.url
    page.get_by_role('button', name='+ Позиция', exact=True).first.click()
    holding = page.locator('#dlg-holding')
    holding.get_by_role('combobox', name=re.compile('Валюта')).select_option('RUB')
    holding.get_by_label('Тикер').fill('SYNTH')
    holding.get_by_label('Количество').fill('2,5')
    holding.get_by_label('Средняя цена покупки').fill('30')
    holding.get_by_label('Текущая цена').fill('40')
    holding.get_by_role('button', name='Сохранить', exact=True).click()
    row = page.get_by_role('row').filter(has_text='SYNTH')
    expect(row.get_by_role('cell', name='2,5', exact=True)).to_be_visible()
    value = row.get_by_role('cell', name=re.compile(r'^100(?:,00)?\s*₽$'))
    expect(value).to_be_visible()

    page.get_by_role('button', name='Заблокировать', exact=True).click()
    page.goto(account_url)
    expect(page.get_by_role('heading', name='Хранилище заблокировано')).to_be_visible()
    page.get_by_label('Пароль', exact=True).fill('synthetic-wealth')
    page.get_by_role('button', name='Открыть', exact=True).click()
    expect(page).to_have_url(account_url)
    expect(value).to_be_visible()


def check_property_language(page):
    page.get_by_title('Добавить актив', exact=True).click()
    form = page.locator('#dlg-account')
    form.get_by_label('Название', exact=True).fill('Тестовая квартира')
    form.get_by_role('button', name='Сохранить', exact=True).click()
    expect(page.get_by_role('heading', name='Тестовая квартира')).to_be_visible()
    property_url = page.url
    expect(page.get_by_text('Расходы', exact=True)).to_be_visible()
    page.get_by_role('link', name='Настройки', exact=True).click()
    settings = page.locator('#general')
    settings.get_by_role('combobox').select_option('en')
    settings.get_by_role('button', name='Сохранить').click()
    expect(page.get_by_role('heading', name='General', exact=True)).to_be_visible()
    page.goto(property_url)
    report = page.locator('section').filter(has=page.get_by_role('heading', name='Property cash flow over 12 months'))
    expect(report.get_by_text('Expenses', exact=True)).to_be_visible()


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        app = create_app({'TESTING': True, 'VAULT_PATH': root / 'test.enc', 'LEGACY_DB_PATH': root / 'test.db'})
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            executable = os.environ.get('MACRO_CHROMIUM_EXECUTABLE') or (shutil.which('chromium') if not os.environ.get('CI') else None)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=executable, args=['--no-sandbox'])
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                check_account(page, f'http://127.0.0.1:{server.server_port}')
                check_property_language(page)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.extensions['wealth_manager'].close()
    print('Chromium OK: first vault, account, decimal holding, valuation, lock, reopen and RU/EN property expenses.')


if __name__ == '__main__':
    main()
