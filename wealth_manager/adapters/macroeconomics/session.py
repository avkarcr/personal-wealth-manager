"""Vault generation guard: a response must never enter a different unlocked vault."""
from contextlib import contextmanager
from wealth_manager.adapters.persistence.unit_of_work import VaultUnitOfWork
from wealth_manager.modules.macroeconomics.refresh import RefreshError

class Session:
    def __init__(self,vault):self.vault=vault
    def current(self):
        with self.vault.lock_:
            return self.vault.token if self.vault.unlocked and not self.vault.idle_expired() else None
    def read_state(self,token):
        from wealth_manager.adapters.persistence.macro_data import Repository
        with self.vault.lock_:
            self.check(token)
            return Repository(self.vault.con).refresh_state()
    def check(self,token):
        with self.vault.lock_:
            if not self.vault.unlocked or token!=self.vault.token or self.vault.idle_expired():
                raise RefreshError('Хранилище изменилось или заблокировано. Повторите загрузку')
    @contextmanager
    def write(self,token):
        with self.vault.lock_:
            self.check(token)
            with VaultUnitOfWork(self.vault) as uow:yield uow

    def calculation_snapshot(self,token):
        from wealth_manager.adapters.persistence.macro_data import Reader, Repository
        from wealth_manager.adapters.persistence.macro_calculations import Repository as Calculations
        with self.vault.lock_:
            self.check(token)
            return dict(rows=Reader(self.vault).read(),revision_token=Repository(self.vault.con).revision_token(),runs=Calculations(self.vault.con).recent())
    def saved_calculation(self,token,rid):
        from wealth_manager.adapters.persistence.macro_calculations import Repository
        with self.vault.lock_:
            self.check(token)
            return Repository(self.vault.con).get(rid)
