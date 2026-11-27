from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from vault import MAGIC, Vault, WrongPassword
from db import init_db
from wealth_manager.bootstrap import build_services
from wealth_manager.adapters.persistence.unit_of_work import VaultUnitOfWork
from wealth_manager.modules.accounts.domain import Purchase
from wealth_manager.modules.entities.domain import RecordDraft as Account
from datetime import date
from decimal import Decimal

class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.vault=Vault(str(Path(self.tmp.name)/'test.enc'))
        init_db(self.vault.create('password123'));self.vault.save(force=True)
    def tearDown(self): self.vault.lock();self.tmp.cleanup()

    def test_wrong_password_and_tampering(self):
        header = Path(self.vault.path).read_bytes()
        self.assertEqual(struct.unpack_from('>III', header, len(MAGIC) + 16), (2 ** 15, 8, 1))
        self.vault.lock()
        self.assertFalse(self.vault.check_password('password123'))
        with self.assertRaises(WrongPassword):self.vault.unlock('bad password')
        path=Path(self.vault.path);data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
        with self.assertRaises(WrongPassword):self.vault.unlock('password123')
        self.assertFalse(self.vault.unlocked)

    def test_password_change_preserves_data(self):
        build_services(self.vault)['save_account'].execute(Account('Keep me'))
        self.vault.change_password('password123','newpassword123');self.vault.lock()
        with self.assertRaises(WrongPassword):self.vault.unlock('password123')
        self.vault.unlock('newpassword123')
        self.assertEqual(self.vault.con.execute('SELECT name FROM accounts').fetchone()[0],'Keep me')

    def test_failed_password_rotation_preserves_credentials_and_can_be_retried(self):
        build_services(self.vault)['save_account'].execute(Account('Durable account'))
        path = Path(self.vault.path)
        original = path.read_bytes()
        token = self.vault.token
        self.vault.con.execute("INSERT INTO settings(key,value) VALUES ('pending','kept')")
        self.vault.con.commit()

        for operation in ('fsync', 'remove', 'replace'):
            with self.subTest(operation=operation):
                with patch('vault.os.' + operation, side_effect=OSError('disk error')):
                    with self.assertRaises(OSError):
                        self.vault.change_password('password123', 'rotated123')
                self.assertEqual(path.read_bytes(), original)
                self.assertTrue(self.vault.check_password('password123'))
                self.assertFalse(self.vault.check_password('rotated123'))
                self.assertEqual(self.vault.token, token)
                self.assertTrue(self.vault.dirty())
                self.assertFalse(Path(str(path) + '.tmp').exists())

        reader = Vault(str(path))
        self.addCleanup(reader.lock)
        reader.unlock('password123')
        self.assertEqual(reader.con.execute('SELECT name FROM accounts').fetchone()[0], 'Durable account')
        reader.lock()
        self.vault.change_password('password123', 'rotated123')
        self.assertNotEqual(self.vault.token, token)
        self.assertFalse(Path(str(path) + '.bak').exists())
        with self.assertRaises(WrongPassword):
            reader.unlock('password123')
        reader.unlock('rotated123')
        self.assertEqual(reader.con.execute("SELECT value FROM settings WHERE key='pending'").fetchone()[0], 'kept')

    def test_locked_uow_rejected_and_lock_released(self):
        self.vault.lock()
        with self.assertRaises(RuntimeError):
            with VaultUnitOfWork(self.vault):pass
        self.vault.unlock('password123')
        build_services(self.vault)['change_language'].execute('en')

    def test_active_transaction_rejected_without_losing_changes(self):
        self.vault.con.execute("INSERT INTO settings(key,value) VALUES ('pending','value')")
        with self.assertRaises(RuntimeError):
            with VaultUnitOfWork(self.vault):pass
        self.assertTrue(self.vault.con.in_transaction)
        self.vault.con.rollback()

    def test_uncommitted_context_rolls_back(self):
        with VaultUnitOfWork(self.vault) as uow:uow.accounts.save(Account('Discard'))
        self.assertEqual(self.vault.con.execute('SELECT count(*) FROM accounts').fetchone()[0],0)

    def test_nonfinite_values_and_negative_purchase_are_rejected(self):
        service=build_services(self.vault)['save_account']
        for value in ('NaN','Infinity','-Infinity'):
            with self.assertRaises(ValueError):service.execute(Account('X',current_value=Decimal(value)))
            with self.assertRaises(ValueError):service.execute(Account('X',tax_rate=Decimal(value)))
        with self.assertRaises(ValueError):service.execute(Account('X',kind='realty'),Purchase(Decimal('-1'),date.today()))

    def test_idle_expiration(self):
        self.vault.idle_minutes=1
        with patch('vault.time.time',return_value=self.vault.last_activity+61):self.assertTrue(self.vault.idle_expired())
        self.vault.idle_minutes=0
        self.assertFalse(self.vault.idle_expired())
