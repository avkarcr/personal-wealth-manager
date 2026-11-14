from datetime import date
from wealth_manager.shared.decimal import input_decimal


def finite(*values):
    for value in values: input_decimal(value)


def valid_date(value, *, optional=False):
    if optional and not value:
        return
    date.fromisoformat(value)
