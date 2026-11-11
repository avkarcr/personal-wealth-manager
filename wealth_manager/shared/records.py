"""Validation shared by manually maintained reference records."""
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

class RecordError(ValueError): pass

def required(value):
    if not value.strip(): raise RecordError('Заполните обязательные поля')

def country_code(value):
    if len(value)!=2 or not value.isascii() or not value.isalpha() or value!=value.upper():
        raise RecordError('Укажите двухбуквенный код страны')

def currency_code(value):
    if not 2<=len(value)<=10 or not value.isascii() or not value.isalnum() or value!=value.upper():
        raise RecordError('Некорректный код валюты')

def number(value, minimum=None, maximum=None):
    if not value.is_finite() or (minimum is not None and value<minimum) or (maximum is not None and value>maximum):
        raise RecordError('Число вне допустимого диапазона')

def money(value):
    # Bound manually entered amounts to keep Decimal estimates and UI formatting safe.
    number(value,Decimal('0'),Decimal('1000000000000000000'))

def year_number(value):
    if not 1900<=value<=9999: raise RecordError('Некорректный налоговый год')

def iso_date(value):
    try:
        if date.fromisoformat(value).isoformat()!=value: raise ValueError(value)
    except ValueError: raise RecordError('Некорректная дата')

def website(value):
    if value:
        try:
            parsed=urlsplit(value)
            valid=parsed.scheme in ('https','http') and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError: valid=False
        if not valid: raise RecordError('Ссылка должна начинаться с http:// или https://')
