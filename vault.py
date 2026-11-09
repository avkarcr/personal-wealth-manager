"""Зашифрованное хранилище.

База целиком живёт в памяти процесса. На диске — только файл invest.enc:
    MAGIC | salt(16) | N(4) r(4) p(4) | nonce(12) | AES-256-GCM(шифротекст + тег)
Ключ получается из пароля через scrypt (N=2^15, r=8, p=1). Заголовок защищён как AAD,
поэтому подмена параметров или повреждение файла обнаруживаются.
Без пароля восстановить данные невозможно.
"""
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import struct
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"KAPITAL-ENC-1\n"
SCRYPT = (2 ** 15, 8, 1)


class WrongPassword(Exception):
    pass


def derive_key(password: str, salt: bytes, n=SCRYPT[0], r=SCRYPT[1], p=SCRYPT[2]) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def _connect_memory():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


class Vault:
    def __init__(self, path, idle_minutes=15):
        self.path = path
        self.idle_minutes = idle_minutes
        self.con = None
        self._key = None
        self._salt = None
        self._params = SCRYPT
        self.token = None          # токен сессии браузера, выдаётся при разблокировке
        self.last_activity = 0.0
        self._saved_changes = 0
        self.lock_ = threading.RLock()

    # ── состояние ──
    @property
    def exists(self):
        return os.path.exists(self.path)

    @property
    def unlocked(self):
        return self.con is not None

    def touch(self):
        self.last_activity = time.time()

    def idle_expired(self):
        return self.unlocked and self.idle_minutes > 0 and time.time() - self.last_activity > self.idle_minutes * 60

    # ── создание / открытие ──
    def create(self, password, import_plain_path=None):
        """Новое хранилище. Если указан путь к старой незашифрованной базе — переносим её."""
        with self.lock_:
            con = _connect_memory()
            if import_plain_path and os.path.exists(import_plain_path):
                src = sqlite3.connect(import_plain_path)
                src.backup(con)
                src.close()
            self._salt = os.urandom(16)
            self._params = SCRYPT
            self._key = derive_key(password, self._salt, *self._params)
            self.con = con
            self.con.execute("PRAGMA foreign_keys = ON")
            self._start_session()
            return con

    def unlock(self, password):
        with self.lock_:
            with open(self.path, "rb") as f:
                blob = f.read()
            if not blob.startswith(MAGIC):
                raise ValueError("Файл не похож на хранилище «Капитала»")
            off = len(MAGIC)
            salt = blob[off:off + 16]; off += 16
            n, r, p = struct.unpack(">III", blob[off:off + 12]); off += 12
            nonce = blob[off:off + 12]; off += 12
            header = blob[:off]
            key = derive_key(password, salt, n, r, p)
            try:
                payload = AESGCM(key).decrypt(nonce, blob[off:], header)
            except InvalidTag:
                raise WrongPassword()
            con = _connect_memory()
            kind, data = payload[:1], payload[1:]
            if kind == b"S":
                con.deserialize(data)
            else:
                con.executescript(data.decode("utf-8"))
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            self.con, self._key, self._salt, self._params = con, key, salt, (n, r, p)
            self._start_session()
            return con

    def _start_session(self):
        self.token = secrets.token_urlsafe(32)
        self._saved_changes = self.con.total_changes
        self.touch()

    def check_password(self, password):
        if not self._salt:
            return False
        return hmac.compare_digest(derive_key(password, self._salt, *self._params), self._key)

    # ── сохранение ──
    def dirty(self):
        return self.unlocked and self.con.total_changes != self._saved_changes

    def save(self, force=False):
        with self.lock_:
            if not self.unlocked or (not force and not self.dirty()):
                return
            if hasattr(self.con, "serialize"):
                payload = b"S" + self.con.serialize()
            else:  # Python < 3.11
                payload = b"D" + "\n".join(self.con.iterdump()).encode("utf-8")
            nonce = os.urandom(12)
            header = MAGIC + self._salt + struct.pack(">III", *self._params) + nonce
            blob = header + AESGCM(self._key).encrypt(nonce, payload, header)
            tmp = self.path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(self.path):
                shutil.copyfile(self.path, self.path + ".bak")   # предыдущая версия, тоже зашифрована
            os.replace(tmp, self.path)
            self._saved_changes = self.con.total_changes

    def change_password(self, old, new):
        with self.lock_:
            if not self.check_password(old):
                raise WrongPassword()
            self._salt = os.urandom(16)
            self._params = SCRYPT
            self._key = derive_key(new, self._salt, *self._params)
            self.save(force=True)
            bak = self.path + ".bak"
            if os.path.exists(bak):   # старая копия зашифрована старым паролем — убираем
                os.remove(bak)
            self.token = secrets.token_urlsafe(32)

    def lock(self):
        with self.lock_:
            if self.unlocked:
                self.save()
                self.con.close()
            self.con = None
            self._key = None
            self.token = None


def wipe_file(path):
    """Перезаписывает файл нулями и удаляет (на SSD гарантии нет, но лучше, чем просто удалить)."""
    if not os.path.exists(path):
        return
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(b"\0" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    os.remove(path)
