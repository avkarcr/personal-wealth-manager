"""Public read-only market APIs. No balances, credentials or order execution."""
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from wealth_manager.modules.market_data.domain import Quote, ProviderError, positive, symbol


class HttpClient:
    def get(self,url):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'PersonalWealthManager/1.0'})
            with urllib.request.urlopen(req,timeout=10) as response:
                body=response.read(2_000_001)
            if len(body)>2_000_000: raise ValueError('Response too large')
            return body
        except Exception as error:
            raise ProviderError('Market data request failed') from error
    def json(self,url):
        try: return json.loads(self.get(url))
        except (ValueError,TypeError) as error: raise ProviderError('Invalid JSON response') from error


class CryptoProvider:
    def __init__(self,http): self.http=http
    def quote(self,ticker,exchanges):
        ticker=symbol(ticker)
        if not exchanges: return None
        if ticker=='USDT': return Quote(1.0,'USDT','parity')
        for exchange in exchanges:
            try:
                if exchange=='binance':
                    data=self.http.json(f'https://api.binance.com/api/v3/ticker/price?symbol={ticker}USDT')
                    value=data.get('price')
                elif exchange=='bybit':
                    data=self.http.json(f'https://api.bybit.com/v5/market/tickers?category=spot&symbol={ticker}USDT')
                    value=(data.get('result',{}).get('list') or [{}])[0].get('lastPrice')
                elif exchange=='okx':
                    data=self.http.json(f'https://www.okx.com/api/v5/market/ticker?instId={ticker}-USDT')
                    value=(data.get('data') or [{}])[0].get('last')
                else: continue
                return Quote(positive(value),'USDT',exchange)
            except (ProviderError,ValueError,TypeError,KeyError,AttributeError,IndexError):
                continue
        return None


def _iss_rows(block):
    cols = block.get("columns", [])
    return [dict(zip(cols, r)) for r in block.get("data", [])]

PRIMARY_BOARDS = ['TQBR','TQTF','TQCB','TQOB','TQIR','TQOD','TQOY','TQTD','TQTE']

class MoexProvider:
    def __init__(self,http): self.http=http
    def price(self, ticker):
        ticker = symbol(ticker)
        try:
            return self._price(ticker)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
            raise ProviderError('Invalid stock quote response') from error

    def _price(self, ticker):
        for market in ("shares", "bonds"):
            url = (f"https://iss.moex.com/iss/engine/stock/markets/{market}/securities/{ticker}.json"
                   "?iss.meta=off&iss.only=securities,marketdata")
            data = self.http.json(url)
            secs = {s["BOARDID"]: s for s in _iss_rows(data.get("securities", {}))}
            mds = _iss_rows(data.get("marketdata", {}))
            if not secs:
                continue
            mds.sort(key=lambda m: PRIMARY_BOARDS.index(m["BOARDID"]) if m.get("BOARDID") in PRIMARY_BOARDS else 99)
            for md in mds:
                sec = secs.get(md.get("BOARDID"), {})
                p = md.get("LAST") or md.get("MARKETPRICE") or md.get("LCURRENTPRICE") or sec.get("PREVPRICE")
                if p:
                    if market == "bonds":
                        p = p * (sec.get("FACEVALUE") or 1000) / 100
                    return positive(p)
        return None


class CbrProvider:
    def __init__(self,http): self.http=http
    def rates(self,day=None):
        suffix=''
        if day:
            parsed=date.fromisoformat(day)
            suffix='?date_req='+parsed.strftime('%d/%m/%Y')
        try:
            root=ET.fromstring(self.http.get('https://www.cbr.ru/scripts/XML_daily.asp'+suffix))
            values={'RUB':1.0}
            for v in root.findall('Valute'):
                nominal=positive(v.findtext('Nominal').replace(',','.'))
                values[symbol(v.findtext('CharCode'))]=positive(positive(v.findtext('Value').replace(',','.'))/nominal)
            return values,root.get('Date')
        except (ET.ParseError,ValueError,TypeError,AttributeError) as error:
            raise ProviderError('Invalid central bank response') from error
