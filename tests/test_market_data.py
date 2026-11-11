import json
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import Mock, patch
from werkzeug.datastructures import MultiDict
from db import init_db
from wealth_manager.factory import create_app
from wealth_manager.bootstrap import build_services
from wealth_manager.modules.entities.domain import RecordDraft as Account
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.market_data.domain import Quote, ProviderError, NoExchangeSelected, MissingRate, positive, symbol
from wealth_manager.adapters.market_data.providers import HttpClient, CryptoProvider, MoexProvider, CbrProvider

CBR=b'<ValCurs Date="10.11.2026"><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>90,50</Value></Valute><Valute><CharCode>JPY</CharCode><Nominal>100</Nominal><Value>60,0</Value></Valute></ValCurs>'

class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=create_app({'TESTING':True,'VAULT_PATH':str(Path(self.tmp.name)/'test.enc')})
        self.runtime=self.app.extensions['wealth_manager'];self.addCleanup(self.runtime.close)
        self.vault=self.runtime.vault;init_db(self.vault.create('password123'));self.vault.save(force=True)
        self.s=self.runtime.services;self.market=self.s['market_data']
        self.market.crypto=Mock();self.market.stocks=Mock();self.market.cbr=Mock()
        self.client=self.app.test_client()
        with self.client.session_transaction() as s:s['vault']=self.vault.token
        self.aid=self.s['save_account'].execute(Account('Crypto',kind='crypto'))

    def test_country_profiles_persist_and_never_silently_fallback(self):
        with self.assertRaises(NoExchangeSelected):self.market.refresh_account(self.aid,'crypto')
        self.market.save_preferences('ru',['bybit','okx'])
        self.assertEqual(self.market.active_exchanges(),('bybit','okx'))
        with self.assertRaises(ValueError):self.market.save_preferences('RU',['binance'])
        with self.assertRaises(ValueError):self.market.save_preferences('CA',['bybit'])
        for country,selected in [('RUSSIA',[]),('RU',['x']),('RU',['okx','okx'])]:
            with self.assertRaises(ValueError):self.market.save_preferences(country,selected)
        self.market.save_preferences('CA',[])
        with self.assertRaises(NoExchangeSelected):self.market.active_exchanges()
        self.assertEqual(self.market.preferences('RU')['exchanges'],('bybit','okx'))
        self.assertEqual(self.market.preferences()['country'],'CA')
        self.vault.lock();self.vault.unlock('password123')
        self.assertEqual(self.market.preferences('RU')['exchanges'],('bybit','okx'))
        self.assertEqual(self.market.preferences()['country'],'CA')
        for e in self.market.catalog.exchanges:
            if e['id']=='bybit':e['excluded_countries'].append('RU')
        self.assertEqual(self.market.preferences('RU')['exchanges'],('okx',))

    def test_country_ui_preview_is_read_only_and_validates_post(self):
        self.client.post('/settings/general',data={'language':'en'})
        page=self.client.get('/settings?country=ru').get_data(as_text=True)
        self.assertIn('Country and crypto exchanges',page);self.assertIn('Excluded for this country',page)
        self.assertEqual(self.market.preferences()['country'],'')
        self.assertEqual(self.client.post('/settings/market',data={'country':'RU','exchanges':'binance'}).status_code,400)
        r=self.client.post('/settings/market',data=MultiDict([('country','RU'),('exchanges','bybit'),('exchanges','okx')]))
        self.assertEqual(r.status_code,302);self.assertEqual(self.market.active_exchanges(),('bybit','okx'))
        self.assertIn('checked',self.client.get('/settings').get_data(as_text=True))

    def test_crypto_quotes_update_only_successful_positions_and_report_source(self):
        self.market.save_preferences('RU',['bybit']);self.market.save_rates({'USD':100})
        pid=self.s['positions'].save(Position(self.aid,ticker='BTC',currency='RUB',price=1))
        self.s['positions'].save(Position(self.aid,ticker='ETH',currency='USD',price=2))
        self.s['positions'].save(Position(self.aid,ticker='X',currency='ZZZ',price=3))
        self.s['positions'].save(Position(self.aid,asset_class='Деньги'))
        self.market.crypto.quote.side_effect=[Quote(10,'USDT','bybit'),None,Quote(2,'USDT','bybit')]
        result=self.market.refresh_account(self.aid,'crypto')
        self.assertEqual(result['count'],1);self.assertEqual(result['failed'],['ETH','X'])
        self.assertEqual(self.vault.con.execute('SELECT price FROM holdings WHERE id=?',(pid,)).fetchone()[0],1000)
        self.assertEqual(self.market.crypto.quote.call_args_list[0].args,('BTC',('bybit',)))
        self.market.crypto.quote.side_effect=None;self.market.crypto.quote.return_value=Quote(11,'USDT','bybit')
        r=self.client.post(f'/accounts/{self.aid}/crypto');self.assertEqual(r.status_code,302)
        with self.client.session_transaction() as session:self.assertTrue(any('bybit' in m for _,m in session['_flashes']))

    def test_rate_commands_preserve_selected_base(self):
        self.market.save_rates({'USD':100,'BTC':1},'USD')
        self.market.save_preferences('RU',['okx']);self.market.crypto.quote.return_value=Quote(2,'USDT','okx')
        self.assertEqual(self.market.refresh_crypto_rates()['count'],1)
        self.assertEqual(self.vault.con.execute("SELECT rate FROM rates WHERE currency='BTC'").fetchone()[0],200)
        self.market.crypto.quote.return_value=None;self.assertEqual(self.market.refresh_crypto_rates()['failed'],['BTC'])
        self.market.cbr.rates.return_value=({'USD':90.5,'RUB':1,'ZZZ':5},'10.11.2026')
        self.assertEqual(self.market.refresh_cbr()['count'],1)
        self.assertEqual(self.market.historical_rate('USD','2026-11-10')['rate'],90.5)
        self.market.delete_rate('USD');self.assertEqual(self.s['reports'].navigation()['base'],'USD')
        self.market.delete_rate('RUB');self.assertIn('RUB',self.s['reports'].navigation()['currencies'])
        with self.assertRaises(MissingRate):self.market.refresh_crypto_rates()
        self.assertEqual(self.market.refresh_account(self.aid,'crypto')['count'],0)
        with self.assertRaises(ValueError):self.market.save_rates({'USD':float('nan')})
        with self.assertRaises(ValueError):self.market.save_rates({},'MISSING')

    def test_atomic_preferences_and_prices_on_disk_failure(self):
        self.market.save_preferences('RU',['okx'])
        self.s['positions'].save(Position(self.aid,ticker='BTC',price=1))
        self.market.crypto.quote.return_value=Quote(10,'USDT','okx')
        for action in (lambda:self.market.save_preferences('CA',[]),lambda:self.market.refresh_account(self.aid,'crypto'),lambda:self.market.save_rates({'USD':999}),lambda:self.market.delete_rate('USD')):
            before=list(self.vault.con.iterdump());encrypted=Path(self.vault.path).read_bytes()
            with patch('vault.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):action()
            self.assertEqual(list(self.vault.con.iterdump()),before);self.assertEqual(Path(self.vault.path).read_bytes(),encrypted)

    def test_market_routes_error_and_success_contracts(self):
        self.assertEqual(self.client.post(f'/accounts/{self.aid}/crypto').status_code,302)
        self.assertEqual(self.client.post('/accounts/999/moex').status_code,404)
        self.assertEqual(self.client.post('/rates/save',data={'rate_USD':'bad','new_currency':'BTC','new_rate':'1','base_currency':'RUB'}).status_code,302)
        self.assertEqual(self.client.post('/rates/save',data={'rate_USD':'NaN'}).status_code,400)
        self.client.post('/settings/market',data={'country':'RU','exchanges':'okx'})
        self.market.crypto.quote.return_value=None
        self.assertEqual(self.client.post('/rates/crypto').status_code,302)
        self.market.delete_rate('USD');self.assertEqual(self.client.post('/rates/crypto').status_code,302)
        self.market.cbr.rates.side_effect=ProviderError('offline')
        self.assertEqual(self.client.post('/rates/cbr').status_code,302)
        self.assertEqual(self.client.get('/api/cbr').status_code,502)
        self.market.cbr.rates.side_effect=ValueError('date')
        self.assertEqual(self.client.get('/api/cbr?date=bad').status_code,400)
        self.market.cbr.rates.side_effect=None;self.market.cbr.rates.return_value=({'RUB':1},'today')
        self.assertEqual(self.client.get('/api/cbr?cur=XYZ').status_code,404)
        self.assertEqual(self.client.get('/api/cbr?cur=RUB').json['rate'],1)
        self.assertEqual(self.client.post('/rates/cbr').status_code,302)
        self.assertEqual(self.client.post('/rates/BTC/delete').status_code,302)
        self.s['positions'].save(Position(self.aid,ticker='SBER',price=1))
        self.market.stocks.price.return_value=250
        self.assertEqual(self.client.post(f'/accounts/{self.aid}/moex').status_code,302)
        self.market.stocks.price.side_effect=ProviderError('offline')
        self.assertEqual(self.market.refresh_account(self.aid,'moex')['count'],0)
        with self.assertRaises(ValueError):self.market.refresh_account(self.aid,'invalid')

class ProviderTests(unittest.TestCase):
    def test_selected_exchange_fallback_and_no_hidden_requests(self):
        http=Mock();provider=CryptoProvider(http)
        http.json.side_effect=[ProviderError('offline'),{'data':[{'last':'123'}]}]
        quote=provider.quote('btc',('bybit','okx'))
        self.assertEqual((quote.price,quote.provider),(123,'okx'))
        urls=[c.args[0] for c in http.json.call_args_list]
        self.assertFalse(any('binance' in u for u in urls));self.assertIn('BTCUSDT',urls[0])
        http.reset_mock();self.assertIsNone(provider.quote('BTC',()));self.assertEqual(provider.quote('USDT',('okx',)).price,1);http.json.assert_not_called()
        http.json.side_effect=None;http.json.return_value={'price':'NaN'}
        self.assertIsNone(provider.quote('BTC',('unknown','binance')))
        http.json.return_value={'price':'10'};self.assertEqual(provider.quote('BTC',('binance',)).price,10)
        http.json.return_value={'result':{'list':[{'lastPrice':'11'}]}};self.assertEqual(provider.quote('BTC',('bybit',)).price,11)
        with self.assertRaises(ValueError):provider.quote('../BTC',('okx',))
        # Stablecoins other than the quote currency get real market quotes (depegging).
        http.json.return_value={'data':[{'last':'0.8'}]};self.assertEqual(provider.quote('USDC',('okx',)).price,0.8)

    def test_cbr_nominal_historical_and_invalid_payload(self):
        http=Mock();http.get.return_value=CBR;p=CbrProvider(http)
        rates,day=p.rates('2026-11-10');self.assertEqual(rates['JPY'],0.6);self.assertEqual(day,'10.11.2026')
        self.assertIn('date_req=10/11/2026',http.get.call_args.args[0])
        with self.assertRaises(ValueError):p.rates('bad')
        for body in (b'bad',b'<ValCurs><Valute/></ValCurs>',CBR.replace(b'<Nominal>1</Nominal>',b'<Nominal>0</Nominal>')):
            http.get.return_value=body
            with self.assertRaises(ProviderError):p.rates()

    def test_moex_boards_and_bond_nominal(self):
        http=Mock();p=MoexProvider(http)
        block={'securities':{'columns':['BOARDID','PREVPRICE','FACEVALUE'],'data':[['TQBR',90,1000],['OTHER',10,1000]]},'marketdata':{'columns':['BOARDID','LAST'],'data':[['OTHER',10],['TQBR',100]]}}
        http.json.return_value=block;self.assertEqual(p.price('SBER'),100)
        http.json.side_effect=[{},block];self.assertEqual(p.price('BOND'),1000)
        http.json.side_effect=[{},{}];self.assertIsNone(p.price('NONE'))
        block['marketdata']['data']=[['TQBR',None]];http.json.side_effect=None;http.json.return_value=block
        self.assertEqual(p.price('SBER'),90)
        block['securities']['data']=[['TQBR',None,1000]];self.assertIsNone(p.price('NONE'))
        http.json.return_value=None
        with self.assertRaises(ProviderError):p.price('SBER')

    def test_http_timeout_size_and_invalid_json(self):
        http=HttpClient();response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"price":"1"}'
        with patch('urllib.request.urlopen',return_value=response) as request:
            self.assertEqual(http.json('https://example.test')['price'],'1');self.assertEqual(request.call_args.kwargs['timeout'],10)
            response.read.return_value=b'bad'
            with self.assertRaises(ProviderError):http.json('https://example.test')
            response.read.return_value=b'x'*2_000_001
            with self.assertRaises(ProviderError):http.get('https://example.test')
        with patch('urllib.request.urlopen',side_effect=TimeoutError):
            with self.assertRaises(ProviderError):http.get('https://example.test')
        for value in (0,-1,float('inf')):
            with self.assertRaises(ValueError):positive(value)
