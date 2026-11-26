"""Read the board-specific ISS security record and a bounded daily candle window."""
from datetime import date, datetime, time
from decimal import Decimal, DecimalException
import json
import re
from urllib.parse import urlencode

from wealth_manager.adapters.macroeconomics.moex import Transport, records
from wealth_manager.modules.market_catalog.iss import BOARD, Quote, Reference
from wealth_manager.shared.decimal import input_decimal
from wealth_manager.shared.records import RecordError


ROOT_URL = f'https://iss.moex.com/iss/engines/stock/markets/shares/boards/{BOARD}/securities/'


def text_field(value, limit):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError('Invalid text')
    return value.strip()


def amount(value, *, positive=False):
    if type(value) not in (int, Decimal):
        raise ValueError('Expected a number')
    value = input_decimal(value)
    if value < 0 or (positive and value == 0):
        raise ValueError('Invalid amount')
    return value


class IssProvider:
    def __init__(self, transport=None):
        self.transport = transport or Transport()

    def _rows(self, ticker, suffix, block, columns, limit, **query):
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,35}', ticker):
            raise RecordError('Некорректный код инструмента для ISS')
        url = ROOT_URL + ticker + suffix + '?' + urlencode({'iss.meta': 'off', 'iss.only': block, **query})
        raw = self.transport.get(url)
        try:
            table = json.loads(raw, parse_float=Decimal)[block]
            fields, rows = table['columns'], table['data']
            if (
                not isinstance(fields, list) or not all(isinstance(field, str) for field in fields)
                or not columns <= set(fields)
                or not isinstance(rows, list) or len(rows) > limit
                or not all(isinstance(row, list) for row in rows)
            ):
                raise ValueError('Unexpected table shape')
            return records(table)
        except (KeyError, TypeError, ValueError, DecimalException, RecursionError) as error:
            raise RecordError('Некорректный ответ ISS') from error

    def reference(self, ticker):
        columns = {'SECID', 'BOARDID', 'ISIN', 'REGNUMBER', 'SECNAME', 'PREVDATE', 'PREVPRICE'}
        rows = self._rows(ticker, '.json', 'securities', columns, 1)
        if not rows:
            raise RecordError('ISS не вернул сведения для этого кода и режима торгов')
        row = rows[0]
        try:
            if row['SECID'] != ticker or row['BOARDID'] != BOARD:
                raise ValueError('Wrong security or board')
            isin = text_field(row['ISIN'], 12)
            if isin and not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]', isin):
                raise ValueError('Invalid ISIN')
            reference = Reference(isin, text_field(row['REGNUMBER'], 64), text_field(row['SECNAME'], 256))
            quote = None
            if row['PREVDATE'] is not None and row['PREVPRICE'] is not None:
                day = date.fromisoformat(row['PREVDATE'])
                if day.isoformat() != row['PREVDATE']:
                    raise ValueError('Invalid trading date')
                quote = Quote(day, amount(row['PREVPRICE'], positive=True))
            return reference, quote
        except (TypeError, ValueError) as error:
            raise RecordError('Некорректный ответ ISS') from error

    def candles(self, ticker, start, end):
        columns = {'begin', 'end', 'close', 'value'}
        rows = self._rows(ticker, '/candles.json', 'candles', columns, 30, interval=24,
                          **{'from': start.isoformat(), 'till': end.isoformat()})
        bars = {}
        try:
            for row in rows:
                begin, finish = (datetime.fromisoformat(row[key]) for key in ('begin', 'end'))
                day = begin.date()
                if (begin.tzinfo or finish.tzinfo or not start <= day <= end or day in bars
                        or begin.time() != time.min or finish.date() != day or finish.time() != time(23, 59, 59)):
                    raise ValueError('Invalid daily candle period')
                close = amount(row['close'], positive=True)
                turnover = None if row['value'] is None else amount(row['value'])
                bars[day] = Quote(day, close, basis='candle', turnover=turnover)
        except (TypeError, ValueError) as error:
            raise RecordError('Некорректный ответ ISS') from error
        return [bars[day] for day in sorted(bars)]
