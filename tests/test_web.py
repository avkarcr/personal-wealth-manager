import json
import os
from pathlib import Path
import re
import tempfile
import unittest

# Import the legacy entry point with an isolated vault, never the user's file.
TMP = tempfile.TemporaryDirectory()
os.environ['INVEST_VAULT'] = str(Path(TMP.name) / 'invest.enc')
import db
db.VAULT_PATH = os.environ['INVEST_VAULT']
import app as web
from db import init_db
from wealth_manager.adapters.web.i18n import EN

class WebTests(unittest.TestCase):
    def setUp(self):
        web.VAULT.lock()
        init_db(web.VAULT.create('password123'))
        web.VAULT.save(force=True)
        web.app.config['TESTING'] = True
        self.client = web.app.test_client()
        with self.client.session_transaction() as session:
            session['vault'] = web.VAULT.token

    def test_default_language_and_switch_both_ways(self):
        response = self.client.get('/settings')
        self.assertIn('Общие', response.get_data(as_text=True))
        response = self.client.post('/settings/general', data={'language':'en'}, follow_redirects=True)
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code,200)
        self.assertIn('<html lang="en">',text)
        self.assertIn('Interface language',text)
        self.assertIn('General',text)
        self.assertNotIn('JINJATOKEN',text)
        response = self.client.post('/settings/general',data={'language':'ru'},follow_redirects=True)
        self.assertIn('<html lang="ru">',response.get_data(as_text=True))

    def test_language_survives_unlock_and_invalid_value(self):
        self.client.post('/settings/general',data={'language':'en'})
        self.assertEqual(self.client.post('/settings/general',data={'language':'xx'}).status_code,400)
        self.client.post('/lock')
        self.assertIn('<html lang="en">',self.client.get('/unlock').get_data(as_text=True))
        self.client.post('/unlock',data={'password':'password123'})
        self.assertIn('Interface language',self.client.get('/settings').get_data(as_text=True))

    def test_save_account_route_and_user_data_not_translated(self):
        response=self.client.post('/accounts/save',data={'name':'Капитал <script>','kind':'broker'})
        self.assertEqual(response.status_code,302)
        self.client.post('/settings/general',data={'language':'en'})
        page=self.client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('Капитал &lt;script&gt;',page)
        self.assertIn('value="Деньги">Cash</option>',page)
        self.assertEqual(self.client.post('/accounts/save',data={'name':'X','kind':'bad'}).status_code,400)
        self.assertEqual(self.client.post('/accounts/save',data={'name':'X','id':'999'}).status_code,404)
        self.assertEqual(self.client.post('/accounts/save',data={'name':'X','current_value':'NaN'}).status_code,400)

    def test_all_pages_render_in_both_languages(self):
        urls=['/','/settings','/income','/operations']
        for kind in ('broker','crypto','realty','freelance','other'):
            response=self.client.post('/accounts/save',data={'name':kind,'kind':kind,'purchase_price':'100'})
            urls.append(response.headers['Location'])
        for lang in ('ru','en'):
            self.client.post('/settings/general',data={'language':lang})
            for url in urls:
                with self.subTest(language=lang,url=url):
                    response=self.client.get(url)
                    self.assertEqual(response.status_code,200)
                    self.assertNotIn('JINJATOKEN',response.get_data(as_text=True))

    def test_static_template_catalog_complete(self):
        for p in Path('templates').glob('*.html'):
            for key in re.findall(r"_\('([^']*)'\)",p.read_text()):
                if re.search('[А-Яа-яЁё]',key): self.assertIn(key,EN,(p,key))

    def test_unauthenticated_preferences_write_is_blocked(self):
        self.client.post('/lock')
        self.assertEqual(self.client.post('/settings/general',data={'language':'en'}).status_code,302)

if __name__ == '__main__': unittest.main()
