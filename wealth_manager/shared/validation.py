from datetime import date
from math import isfinite


def finite(*values):
    if not all(isfinite(value) for value in values):
        raise ValueError("Amounts must be finite")


def valid_date(value, *, optional=False):
    if optional and not value:
        return
    date.fromisoformat(value)
