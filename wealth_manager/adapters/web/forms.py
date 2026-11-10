"""HTTP form parsing shared by command adapters and legacy pages."""
from decimal import Decimal
from flask import request, redirect, url_for

def f_str(name, default=""):
    return (request.form.get(name) or default).strip()


def f_float(name, default=0.0):
    v = (request.form.get(name) or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(v) if v else default
    except ValueError:
        return default


def f_decimal(name):
    value = (request.form.get(name) or "0").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    return Decimal(value)


def back(default="dashboard", **kw):
    return redirect(request.form.get("next") or request.referrer or url_for(default, **kw))


