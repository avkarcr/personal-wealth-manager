"""Persist migrations before admitting requests; failed saves restore memory."""
import sqlite3
from db import init_db


def initialize_vault(vault, *, lock_on_failure=False):
    with vault.lock_:
        before=sqlite3.connect(':memory:')
        vault.con.backup(before)
        try:
            init_db(vault.con)
            vault.save(force=True)
        except BaseException:
            vault.con.rollback()
            before.backup(vault.con)
            vault._saved_changes=vault.con.total_changes
            if lock_on_failure:vault.lock()
            raise
        finally:
            before.close()
