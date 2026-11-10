"""Explicit ownership of a vault and its optional background worker."""
import logging
import threading

logger = logging.getLogger(__name__)


class ApplicationRuntime:
    def __init__(self, vault, services, *, poll_seconds=30):
        if poll_seconds <= 0:
            raise ValueError("Auto-lock poll interval must be positive")
        self.vault = vault
        self.services = services
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread = None
        self._closed = False

    @property
    def closed(self):
        return self._closed

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def get_connection(self):
        """Caller must hold vault.lock_ while using the shared connection."""
        if self._closed or not self.vault.unlocked:
            raise RuntimeError("Хранилище заблокировано")
        return self.vault.con

    def lock_if_idle(self):
        with self.vault.lock_:
            if not self._closed and self.vault.idle_expired():
                self.vault.lock()

    def _run(self):
        while not self._stop.wait(self.poll_seconds):
            try:
                self.lock_if_idle()
            except Exception:
                # A failed disk write must not silently kill auto-lock or discard
                # the decrypted database. Vault.lock retains it on save failure.
                logger.exception("Auto-lock failed; the vault remains open for retry")

    def start(self):
        """Idempotent start; app construction alone never launches a thread."""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Application runtime is closed")
            if self.running:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="wealth-manager-autolock", daemon=True,
            )
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                raise

    def close(self):
        """Stop/join the worker, save and lock. Call outside a request context.

        If saving fails, propagate the error and keep the vault accessible so
        the owner can retry close. Never mark an unsaved runtime as closed.
        """
        with self._lifecycle_lock:
            if self._closed:
                return
            self._stop.set()
            if self._thread is not None:
                self._thread.join()
                self._thread = None
            with self.vault.lock_:
                self.vault.lock()
                self._closed = True
