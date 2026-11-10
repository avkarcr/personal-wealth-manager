"""Request serialization, access guard and legacy persistence hooks."""
from flask import abort, flash, g, redirect, request, session, url_for

OPEN_ENDPOINTS = frozenset({"static", "setup", "unlock"})


def install_vault_hooks(app, runtime):
    vault = runtime.vault

    @app.before_request
    def acquire_vault():
        vault.lock_.acquire()
        g.wealth_vault_lock = vault.lock_
        if runtime.closed:
            abort(503)

    @app.before_request
    def guard():
        if request.endpoint in OPEN_ENDPOINTS:
            return
        if vault.idle_expired():
            vault.lock()
            flash("Хранилище заблокировано из-за бездействия")
        if not vault.exists and not vault.unlocked:
            return redirect(url_for("setup"))
        if not vault.unlocked or session.get("vault") != vault.token:
            return redirect(url_for("unlock", next=request.full_path if request.method == "GET" else None))
        vault.touch()

    @app.after_request
    def persist(response):
        # Transitional support for handlers not yet moved to UnitOfWork.
        if vault.unlocked and vault.dirty():
            vault.save()
        return response

    @app.teardown_request
    def release_vault(error):
        lock = g.pop("wealth_vault_lock", None)
        if lock is not None:
            lock.release()
