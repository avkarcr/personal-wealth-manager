"""HTTP form parsing shared by command adapters and legacy pages."""
from decimal import Decimal
from flask import request, redirect, url_for

def f_str(name, default=""):
    return (request.form.get(name) or default).strip()


def f_decimal(name):
    value = (request.form.get(name) or "0").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if len(value)>200: raise ValueError('Amount is too long')
    from wealth_manager.shared.decimal import input_decimal
    return input_decimal(value)


def back(default="dashboard", **kw):
    return redirect(request.form.get("next") or request.referrer or url_for(default, **kw))


