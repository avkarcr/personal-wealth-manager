"""Isolation and ownership tests; all data lives in temporary directories."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from db import init_db
from vault import Vault
from wealth_manager.factory import create_app


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make_app(self, name='first', **config):
        app = create_app({
            'TESTING': True,
            'VAULT_PATH': self.root / (name + '.enc'),
            'LEGACY_DB_PATH': self.root / (name + '.db'),
            **config,
        })
        self.addCleanup(app.extensions['wealth_manager'].close)
        return app

    def setup_client(self, app):
        client = app.test_client()
        response = client.post('/setup', data={'password': 'password123', 'password2': 'password123'})
        self.assertEqual(response.status_code, 302)
        return client

    def assert_lock_available(self, runtime):
        acquired = []
        def probe():
            ok = runtime.vault.lock_.acquire(timeout=1)
            acquired.append(ok)
            if ok:
                runtime.vault.lock_.release()
        thread = threading.Thread(target=probe, daemon=True)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), 'Request leaked the vault lock')
        self.assertEqual(acquired, [True])

    def test_import_has_no_app_vault_or_thread_side_effects(self):
        code = '''
from unittest.mock import patch
import flask
import vault
with patch.object(flask.Flask, '__init__', side_effect=AssertionError('app created')), \\
     patch.object(vault.Vault, '__init__', side_effect=AssertionError('vault created')), \\
     patch('threading.Thread.start', side_effect=AssertionError('thread started')):
    import app
    import db
    import wealth_manager.factory
    assert not hasattr(app, 'app')
    assert not hasattr(app, 'VAULT')
    assert not hasattr(db, '_vault')
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_factory_is_passive_and_endpoints_are_registered_once(self):
        with patch('threading.Thread.start', side_effect=AssertionError('unexpected thread')):
            a = self.make_app('a')
            b = self.make_app('b')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIsNot(a.extensions['wealth_manager'], b.extensions['wealth_manager'])
        self.assertFalse(a.extensions['wealth_manager'].running)
        rules = lambda app: sorted((r.rule, tuple(sorted(r.methods))) for r in app.url_map.iter_rules())
        self.assertEqual(rules(a), rules(b))
        self.assertEqual(sum(r.rule == '/accounts/save' for r in a.url_map.iter_rules()), 1)
        with self.assertRaises(RuntimeError):
            a.extensions['wealth_manager'].get_connection()
        self.assertEqual(a.test_client().get('/').headers['Location'], '/setup')
        with a.test_client().get('/static/style.css') as response:
            self.assertEqual(response.status_code, 200)

    def test_two_apps_keep_accounts_preferences_and_tokens_separate(self):
        a, b = self.make_app('a'), self.make_app('b')
        ca, cb = self.setup_client(a), self.setup_client(b)
        ca.post('/accounts/save', data={'name': 'Account A'})
        cb.post('/accounts/save', data={'name': 'Account B'})
        ca.post('/settings/general', data={'language': 'en'})
        self.assertIn('Account A', ca.get('/').get_data(as_text=True))
        self.assertNotIn('Account B', ca.get('/').get_data(as_text=True))
        self.assertIn('Account B', cb.get('/').get_data(as_text=True))
        self.assertIn('<html lang="en">', ca.get('/settings').get_data(as_text=True))
        self.assertIn('<html lang="ru">', cb.get('/settings').get_data(as_text=True))
        ra, rb = (app.extensions['wealth_manager'] for app in (a, b))
        self.assertIsNot(ra.get_connection(), rb.get_connection())
        self.assertNotEqual(a.secret_key, b.secret_key)
        with cb.session_transaction() as session:
            session['vault'] = ra.vault.token
        self.assertIn('/unlock', cb.get('/').headers['Location'])
        ca.post('/lock')
        self.assertFalse(ra.vault.unlocked)
        self.assertTrue(rb.vault.unlocked)

    def test_configuration_is_read_at_construction_and_can_be_overridden(self):
        with patch.dict(os.environ, {'INVEST_VAULT': str(self.root/'env.enc'),
                                     'INVEST_DB': str(self.root/'env.db'), 'INVEST_IDLE_MIN': '7'}):
            app = create_app()
            self.addCleanup(app.extensions['wealth_manager'].close)
            override = self.make_app('override', IDLE_MINUTES=2)
        self.assertEqual(app.extensions['wealth_manager'].vault.path, str(self.root/'env.enc'))
        self.assertEqual(app.config['LEGACY_DB_PATH'], str(self.root/'env.db'))
        self.assertEqual(app.extensions['wealth_manager'].vault.idle_minutes, 7)
        self.assertEqual(override.extensions['wealth_manager'].vault.idle_minutes, 2)

    def test_supplied_vault_is_used_by_services_and_routes(self):
        vault = Vault(str(self.root/'injected.enc'))
        app = create_app({'TESTING': True, 'LEGACY_DB_PATH': str(self.root/'missing.db')}, vault=vault)
        self.addCleanup(app.extensions['wealth_manager'].close)
        client = self.setup_client(app)
        client.post('/accounts/save', data={'name': 'Injected'})
        self.assertIs(app.extensions['wealth_manager'].vault, vault)
        self.assertEqual(vault.con.execute('SELECT name FROM accounts').fetchone()[0], 'Injected')

    def test_shutdown_of_one_app_leaves_other_app_available(self):
        a, b = self.make_app('a'), self.make_app('b')
        self.setup_client(a)
        cb = self.setup_client(b)
        a.extensions['wealth_manager'].close()
        self.assertEqual(a.test_client().get('/').status_code, 503)
        self.assertEqual(cb.get('/').status_code, 200)
        self.assert_lock_available(a.extensions['wealth_manager'])

    def test_idle_guard_locks_without_background_worker(self):
        app = self.make_app()
        client = self.setup_client(app)
        runtime = app.extensions['wealth_manager']
        runtime.vault.last_activity = 0
        self.assertIn('/unlock', client.get('/settings').headers['Location'])
        self.assertFalse(runtime.vault.unlocked)
        self.assert_lock_available(runtime)

    def test_lock_released_after_handler_and_locale_failures(self):
        for point in ('handler', 'locale'):
            with self.subTest(point=point):
                app = self.make_app(point)
                @app.get('/fail')
                def fail():
                    raise RuntimeError('handler failure')
                client = self.setup_client(app)
                runtime = app.extensions['wealth_manager']
                if point == 'handler':
                    with self.assertRaises(RuntimeError): client.get('/fail')
                else:
                    with patch('wealth_manager.adapters.web.i18n.SqlitePreferencesRepository.get_language', side_effect=RuntimeError('locale failure')):
                        with self.assertRaises(RuntimeError): client.get('/settings')
                self.assert_lock_available(runtime)
                self.assertEqual(client.get('/settings').status_code, 200)

    def test_legacy_route_still_persists_after_request(self):
        app = self.make_app()
        client = self.setup_client(app)
        client.post('/accounts/save', data={'name': 'Before', 'kind': 'realty'})
        client.post('/accounts/1/value', data={'current_value': '123'})
        runtime = app.extensions['wealth_manager']
        reader = Vault(runtime.vault.path)
        self.addCleanup(reader.lock)
        reader.unlock('password123')
        self.assertEqual(reader.con.execute('SELECT current_value FROM accounts').fetchone()[0], 123)

    def test_existing_vault_can_be_reopened_by_a_fresh_app(self):
        app = self.make_app('reopen')
        client = self.setup_client(app)
        client.post('/accounts/save', data={'name': 'Saved'})
        client.post('/settings/general', data={'language': 'en'})
        app.extensions['wealth_manager'].close()
        fresh = self.make_app('reopen')
        client = fresh.test_client()
        self.assertEqual(client.post('/unlock', data={'password': 'password123'}).status_code, 302)
        self.assertIn('Saved', client.get('/').get_data(as_text=True))
        self.assertIn('<html lang="en">', client.get('/settings').get_data(as_text=True))

    def test_legacy_import_uses_this_app_configured_path(self):
        path = self.root/'legacy.db'
        con = sqlite3.connect(str(path)); con.row_factory = sqlite3.Row
        init_db(con)
        from wealth_manager.adapters.persistence.entities import SqliteFinancialAccountRepository
        from wealth_manager.modules.accounts.domain import Account
        SqliteFinancialAccountRepository(con).save(Account('Imported'))
        con.commit(); con.close()
        app = self.make_app('target', LEGACY_DB_PATH=path)
        client = self.setup_client(app)
        self.assertIn('Imported', client.get('/').get_data(as_text=True))
        self.assertFalse(path.exists())
