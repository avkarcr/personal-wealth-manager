"""Flask application factory. No vault I/O or worker startup until explicitly requested."""
import os
from pathlib import Path
import secrets

from flask import Flask

from wealth_manager.bootstrap import build_runtime
from wealth_manager.adapters.web.i18n import install_i18n
from wealth_manager.adapters.web.legacy_routes import register_routes
from wealth_manager.adapters.web.commands import register_commands
from wealth_manager.adapters.web.reports import register_reports
from wealth_manager.adapters.web.market_data import register_market_data
from wealth_manager.adapters.web.security import install_vault_hooks


def create_app(config=None, *, vault=None):
    """Build an independent app; optional vault injection is for adapter tests.

    The caller owns app.extensions['wealth_manager']: start() enables background
    auto-lock, close() stops it and saves/locks the vault. Neither is implicit in
    app construction or Flask's per-request teardown.
    """
    root = Path(__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    app.config.from_mapping(
        SECRET_KEY=secrets.token_bytes(32),
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_HTTPONLY=True,
        VAULT_PATH=os.environ.get("INVEST_VAULT", str(root / "invest.enc")),
        LEGACY_DB_PATH=os.environ.get("INVEST_DB", str(root / "invest.db")),
        IDLE_MINUTES=os.environ.get("INVEST_IDLE_MIN", 15),
        AUTOLOCK_POLL_SECONDS=30,
    )
    if config is not None:
        app.config.update(config)
    # Normalize Path values for legacy handlers which append backup suffixes.
    app.config["VAULT_PATH"] = os.fspath(app.config["VAULT_PATH"])
    app.config["LEGACY_DB_PATH"] = os.fspath(app.config["LEGACY_DB_PATH"])
    runtime = build_runtime(app.config, vault=vault)
    app.extensions["wealth_manager"] = runtime
    install_vault_hooks(app, runtime)
    install_i18n(app, runtime.vault)
    register_routes(app, runtime)
    register_commands(app, runtime)
    register_reports(app, runtime)
    register_market_data(app, runtime)
    return app
