"""Composition root: only this layer selects concrete adapters."""
from wealth_manager.adapters.persistence.unit_of_work import VaultUnitOfWork
from wealth_manager.modules.accounts.application import SaveAccount
from wealth_manager.modules.preferences.application import ChangeLanguage

def build_services(vault):
    factory = lambda: VaultUnitOfWork(vault)
    return {"save_account": SaveAccount(factory), "change_language": ChangeLanguage(factory)}


def build_runtime(config, *, vault=None):
    from vault import Vault
    from wealth_manager.adapters.runtime import ApplicationRuntime

    if vault is None:
        vault = Vault(config["VAULT_PATH"], idle_minutes=int(config["IDLE_MINUTES"]))
    return ApplicationRuntime(vault, build_services(vault), poll_seconds=config["AUTOLOCK_POLL_SECONDS"])
