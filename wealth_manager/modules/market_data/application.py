from wealth_manager.shared.classifications import asset_key
from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.modules.reporting.application import rates_for
from .domain import positive, symbol, MissingRate, NoExchangeSelected, ProviderError
from .ports import CryptoQuotes, StockQuotes, CentralBankRates

FIAT = frozenset('RUB USD EUR CNY GBP CHF JPY KZT AED TRY BYN UAH AMD GEL UZS KGS AZN HKD SGD THB INR CAD AUD SEK NOK PLN CZK RSD VND IDR'.split())


class MarketData:
    def __init__(self,uow_factory,reports,catalog,crypto: CryptoQuotes,stocks: StockQuotes,cbr: CentralBankRates,today):
        self.uow_factory,self.reports,self.catalog=uow_factory,reports,catalog
        self.crypto,self.stocks,self.cbr,self.today=crypto,stocks,cbr,today

    def preferences(self,country=None):
        with self.uow_factory() as uow:
            current=uow.market_preferences.get()
            country=country.strip().upper() if country is not None else current['country']
            selected=uow.market_preferences.profile(country)
        return dict(country=country,exchanges=self.catalog.active(country,selected),
                    choices=self.catalog.choices(country),reviewed_at=self.catalog.reviewed_at)

    def save_preferences(self,country,exchanges):
        country=country.strip().upper()
        selected=self.catalog.validate(country,exchanges)
        with self.uow_factory() as uow:
            uow.market_preferences.save(country,selected); uow.commit()

    def active_exchanges(self):
        p=self.preferences()
        if not p['exchanges']: raise NoExchangeSelected('Select country and exchanges in settings')
        return p['exchanges']

    def save_rates(self,values,base=''):
        values={symbol(k):positive(v) for k,v in values.items()}
        with self.uow_factory() as uow:
            known=uow.valuations.rates().r
            if base and base not in set(known)|set(values): raise ValueError('Unknown base currency')
            for code,value in values.items():uow.rates.save(code,value,self.today().isoformat())
            uow.rates.save('RUB',1,self.today().isoformat())
            if base:uow.rates.set_base(base)
            uow.take_snapshot();uow.commit()

    def delete_rate(self,currency):
        with self.uow_factory() as uow:
            uow.rates.delete(currency);uow.commit()

    def refresh_account(self,aid,kind):
        data=self.reports.read();rates=rates_for(data)
        account=next((a for a in data['accounts'] if a['id']==aid),None)
        if account is None: raise EntityNotFound(aid)
        if account['kind'] in ('realty','freelance'):raise ValueError('Quotes require a financial account')
        if kind not in ('crypto','moex'): raise ValueError('Unknown market')
        exchanges=self.active_exchanges() if kind=='crypto' else ()
        usd=rates.r.get('USD')
        if kind=='crypto' and not usd: raise MissingRate('USD')
        prices={}; failed=[]; sources={}
        for h in data['holdings']:
            if h['account_id']!=aid or not h['ticker'] or asset_key(h)=='cash':continue
            try:
                if kind=='crypto':
                    q=self.crypto.quote(h['ticker'],exchanges)
                    if q is None: raise ProviderError('No quote')
                    if h['currency'] not in rates.r: raise MissingRate(h['currency'])
                    # Existing USD approximation is explicit in the interface.
                    price=q.price*usd/rates.r[h['currency']];sources[h['ticker']]=q.provider
                else:
                    price=self.stocks.price(h['ticker']);sources[h['ticker']]='MOEX'
                prices[h['id']]=positive(price)
            except (ProviderError,ValueError,TypeError,KeyError):failed.append(h['ticker'])
        if prices:
            with self.uow_factory() as uow:
                uow.accounts.get(aid)
                for hid,price in prices.items():uow.positions.set_price(hid,aid,price,self.today().isoformat())
                uow.take_snapshot();uow.commit()
        return dict(count=len(prices),failed=failed,sources=sources)

    def refresh_crypto_rates(self):
        exchanges=self.active_exchanges();data=self.reports.read();rates=rates_for(data)
        if not rates.r.get('USD'): raise MissingRate('USD')
        values={};failed=[];sources={}
        for code in rates.r:
            if code in FIAT:continue
            q=self.crypto.quote(code,exchanges)
            if q is None:failed.append(code)
            else:values[code]=round(positive(q.price)*rates.r['USD'],6);sources[code]=q.provider
        if values:self.save_rates(values)
        return dict(count=len(values),failed=failed,sources=sources)

    def refresh_cbr(self):
        values,published=self.cbr.rates()
        known=rates_for(self.reports.read()).r
        selected={k:round(v,4) for k,v in values.items() if k in known and k!='RUB'}
        if selected:self.save_rates(selected)
        return dict(count=len(selected),date=published)

    def historical_rate(self,currency,day):
        values,published=self.cbr.rates(day)
        if currency not in values:raise EntityNotFound(currency)
        return dict(rate=round(values[currency],4),date=published)
