import json

class SqliteMarketPreferences:
    def __init__(self, connection): self.connection=connection
    def value(self,key,default):
        row=self.connection.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default
    def profile(self,country): return json.loads(self.value('crypto_exchanges:'+country,'[]'))
    def get(self):
        country=self.value('country','')
        return dict(country=country,exchanges=self.profile(country))
    def save(self,country,exchanges):
        for key,value in [('country',country),('crypto_exchanges:'+country,json.dumps(exchanges))]:
            self.connection.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))

class SqliteRateRepository:
    def __init__(self, connection): self.connection=connection
    def save(self,currency,rate,day):
        self.connection.execute('INSERT INTO rates(currency,rate,updated_at) VALUES (?,?,?) ON CONFLICT(currency) DO UPDATE SET rate=excluded.rate,updated_at=excluded.updated_at',(currency,1 if currency=='RUB' else rate,day))
        self.save_historical(currency,rate,day)
    def save_historical(self,currency,rate,day):
        self.connection.execute('INSERT INTO historical_rates(date,currency,rate) VALUES (?,?,?) ON CONFLICT(date,currency) DO UPDATE SET rate=excluded.rate',(day,currency,1 if currency=='RUB' else rate))
    def delete(self,currency):
        if currency=='RUB': return
        self.connection.execute('DELETE FROM rates WHERE currency=?',(currency,))
    def set_base(self,currency):
        self.connection.execute("INSERT INTO settings(key,value) VALUES ('base_currency',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(currency,))
