from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from db import init_db
from vault import Vault
from wealth_manager.bootstrap import build_services
from wealth_manager.adapters.runtime import ApplicationRuntime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Vault(str(Path(self.tmp.name) / 'vault.enc'), idle_minutes=1)
        self.runtime = ApplicationRuntime(self.vault, build_services(self.vault), poll_seconds=0.01)
        self.addCleanup(self.runtime.close)
        init_db(self.vault.create('password123'))
        self.vault.save(force=True)

    def test_start_once_and_close_joins_worker_and_saves_pending_data(self):
        self.assertFalse(self.runtime.running)
        self.runtime.start()
        thread = self.runtime._thread
        self.runtime.start()
        self.assertIs(self.runtime._thread, thread)
        self.assertTrue(thread.is_alive())
        with self.vault.lock_:
            self.vault.con.execute("INSERT INTO settings(key,value) VALUES ('pending','saved')")
            self.vault.con.commit()
        with patch.object(self.vault, 'lock', wraps=self.vault.lock) as lock:
            self.runtime.close()
            self.runtime.close()
            self.assertEqual(lock.call_count, 1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.runtime.running)
        self.assertTrue(self.runtime.closed)
        self.assertFalse(self.vault.unlocked)
        with self.assertRaises(RuntimeError): self.runtime.get_connection()
        with self.assertRaises(RuntimeError): self.runtime.start()
        reader = Vault(self.vault.path)
        self.addCleanup(reader.lock)
        reader.unlock('password123')
        self.assertEqual(reader.con.execute("SELECT value FROM settings WHERE key='pending'").fetchone()[0], 'saved')

    def test_background_worker_actually_locks_an_idle_vault(self):
        locked = threading.Event()
        original_lock = self.vault.lock
        def lock():
            original_lock()
            locked.set()
        self.vault.last_activity = 0
        with patch.object(self.vault, 'lock', side_effect=lock):
            self.runtime.start()
            self.assertTrue(locked.wait(timeout=2), 'Worker did not lock an idle vault')
            self.runtime.close()
        self.assertFalse(self.vault.unlocked)

    def test_worker_logs_save_error_and_retries(self):
        retried = threading.Event()
        attempts = []
        def check():
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('disk unavailable')
            retried.set()
        with patch.object(self.runtime, 'lock_if_idle', side_effect=check) as tick, \
             patch('wealth_manager.adapters.runtime.logger') as logger:
            self.runtime.start()
            self.assertTrue(retried.wait(timeout=2), 'Worker failed to retry after a disk error')
            self.runtime.close()
            logger.exception.assert_called_once()
            self.assertGreaterEqual(tick.call_count, 2)

    def test_failed_close_keeps_data_available_for_retry(self):
        self.runtime.start()
        self.vault.con.execute("INSERT INTO settings(key,value) VALUES ('pending','keep')")
        self.vault.con.commit()
        original = Path(self.vault.path).read_bytes()
        with patch.object(self.vault, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.runtime.close()
        self.assertFalse(self.runtime.closed)
        self.assertFalse(self.runtime.running)
        self.assertTrue(self.vault.unlocked)
        self.assertEqual(Path(self.vault.path).read_bytes(), original)
        self.assertEqual(self.runtime.get_connection().execute("SELECT value FROM settings WHERE key='pending'").fetchone()[0], 'keep')
        self.runtime.close()
        self.assertTrue(self.runtime.closed)

    def test_request_activity_is_rechecked_under_vault_lock(self):
        attempted = threading.Event()
        def check():
            attempted.set()
            self.runtime.lock_if_idle()
        with self.vault.lock_:
            self.vault.last_activity = 0
            worker = threading.Thread(target=check, daemon=True)
            worker.start()
            self.assertTrue(attempted.wait(timeout=2))
            self.assertTrue(self.vault.unlocked)
            self.vault.touch()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.vault.unlocked)
        self.runtime.close()
        self.runtime.lock_if_idle()  # A closed runtime does not inspect/lock a new connection.

    def test_zero_idle_timeout_disables_locking(self):
        self.vault.idle_minutes = 0
        self.vault.last_activity = 0
        self.runtime.lock_if_idle()
        self.assertTrue(self.vault.unlocked)

    def test_invalid_poll_interval_rejected(self):
        with self.assertRaises(ValueError): ApplicationRuntime(self.vault, {}, poll_seconds=0)

    def test_thread_start_failure_can_be_cleanly_closed(self):
        with patch('wealth_manager.adapters.runtime.threading.Thread') as thread:
            thread.return_value.start.side_effect = OSError('thread unavailable')
            with self.assertRaises(OSError): self.runtime.start()
            self.runtime.close()
            thread.return_value.join.assert_not_called()
        self.assertTrue(self.runtime.closed)
