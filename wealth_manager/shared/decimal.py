"""Decimal arithmetic and explicit rounding; independent of storage and UI."""
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from functools import wraps

ZERO = Decimal(0)
ONE = Decimal(1)


def decimal(value):
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError('Invalid decimal amount') from error
    if not result.is_finite(): raise ValueError('Amounts must be finite')
    return result


def exact_text(value):
    value = decimal(value)
    if not value: return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def arithmetic(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 80
            context.rounding = ROUND_HALF_EVEN
            return function(*args, **kwargs)
    return wrapped


@arithmetic
def rounded(value, places=2):
    return decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def input_decimal(value):
    """User/API input budget: 80 significant digits, 100 fractional places, |x| <= 1e24."""
    value = decimal(value)
    digits=list(value.as_tuple().digits)
    exponent=value.as_tuple().exponent
    while len(digits)>1 and digits[-1]==0:
        digits.pop();exponent+=1
    if value and (len(digits)>80 or exponent < -100 or value.copy_abs()>Decimal('1e24')):
        raise ValueError('Amount exceeds decimal precision limits')
    return value
