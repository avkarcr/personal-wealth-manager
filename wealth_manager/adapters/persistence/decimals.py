"""SQLite boundary: canonical decimal text on disk, Decimal in read models."""
import sqlite3
from decimal import Decimal
from wealth_manager.shared.decimal import decimal, exact_text

COLUMNS = frozenset(('quantity','avg_price','price','amount','current_value','tax_rate',
                     'rent_amount','deposit_amount','received_amount','cb_rate','rate',
                     'value_rub','invested_rub','cost','fee','realized','baseline_rub','ledger_baseline_rub','actual_refund','opening_principal','principal','interest'))


def decimal_row(cursor, values):
    return sqlite3.Row(cursor, tuple(decimal(v) if v is not None and cursor.description[i][0] in COLUMNS else v
                                    for i,v in enumerate(values)))


def configure(connection):
    sqlite3.register_adapter(Decimal, exact_text)
    connection.row_factory = decimal_row
