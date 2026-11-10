import ast
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db import init_db
from vault import Vault
from wealth_manager.bootstrap import build_services
from wealth_manager.modules.accounts.domain import Purchase, AccountNotFound
from wealth_manager.modules.entities.domain import RecordDraft as Account
from wealth_manager.modules.accounts.application import SaveAccount

class MemoryUow:
    def __init__(self):
        self.accounts = self
        self.assets = self
        self.events = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def save(self, account): self.events.append('save'); return 42
    def record_purchase(self, *args): self.events.append('purchase')
    def take_snapshot(self): self.events.append('snapshot')
    def commit(self): self.events.append('commit')

class DomainTests(unittest.TestCase):
    def test_use_case_runs_without_flask_sqlite_or_vault(self):
        uow = MemoryUow()
        result = SaveAccount(lambda: uow).execute(Account('Home', kind='realty'), Purchase(Decimal('100'), date.today()))
        self.assertEqual(result, 42)
        self.assertEqual(uow.events, ['save', 'purchase', 'snapshot', 'commit'])

    def test_invalid_account_does_not_open_transaction(self):
        uow = MemoryUow()
        with self.assertRaises(ValueError):
            SaveAccount(lambda: uow).execute(Account('X', kind='invalid'))
        self.assertEqual(uow.events, [])

    def test_core_has_no_adapter_imports(self):
        for p in Path('wealth_manager/modules').rglob('*.py'):
            for node in ast.walk(ast.parse(p.read_text())):
                names = [node.module or ''] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names] if isinstance(node, ast.Import) else []
                self.assertFalse(any(n.startswith(('flask', 'sqlite3', 'vault', 'calc', 'wealth_manager.adapters')) for n in names), p)

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(str(Path(self.tmp.name) / 'invest.enc'))
        init_db(self.vault.create('password123'))
        self.vault.save(force=True)
        self.services = build_services(self.vault)

    def tearDown(self):
        self.vault.lock()
        self.tmp.cleanup()

    def test_create_edit_and_reopen_property_without_duplicate_purchase(self):
        account = Account('Home', kind='realty', current_value=Decimal('150'))
        purchase = Purchase(Decimal('100'), date(2020, 1, 1))
        aid = self.services['save_account'].execute(account, purchase)
        self.services['save_account'].execute(replace(account, id=aid, name='New name'), purchase)
        self.vault.lock(); self.vault.unlock('password123')
        self.assertEqual(self.vault.con.execute('SELECT name FROM accounts').fetchone()[0], 'New name')
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM cashflows').fetchone()[0], 1)
        self.assertEqual(self.vault.con.execute('SELECT value_rub FROM snapshots').fetchone()[0], 150)

    def test_disk_failure_restores_memory_and_original_encrypted_file(self):
        original = Path(self.vault.path).read_bytes()
        with patch('vault.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.services['save_account'].execute(Account('Home', kind='realty'), Purchase(Decimal('100'), date.today()))
        for table in ('accounts', 'cashflows', 'snapshots'):
            self.assertEqual(self.vault.con.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.assertFalse(self.vault.dirty())
        self.assertEqual(Path(self.vault.path).read_bytes(), original)
        self.vault.lock();self.vault.unlock('password123')
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM accounts').fetchone()[0],0)

    def test_snapshot_failure_rolls_back(self):
        with patch('calc.take_snapshot', side_effect=RuntimeError('snapshot failed')):
            with self.assertRaises(RuntimeError):
                self.services['save_account'].execute(Account('Broker'))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM accounts').fetchone()[0], 0)

    def test_nonexistent_account(self):
        with self.assertRaises(AccountNotFound):
            self.services['save_account'].execute(Account('Missing', id=999))

    def test_language_persists_and_invalid_language_does_not_overwrite(self):
        self.services['change_language'].execute('en')
        with self.assertRaises(ValueError): self.services['change_language'].execute('../../xx')
        self.vault.lock();self.vault.unlock('password123')
        self.assertEqual(self.vault.con.execute("SELECT value FROM settings WHERE key='language'").fetchone()[0], 'en')

if __name__ == '__main__': unittest.main()
