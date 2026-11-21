"""CBR range tables. Fixed HTTPS endpoints, bounded responses, no inferred publications."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlencode
import urllib.request
from wealth_manager.modules.macroeconomics.data import Indicator,Source,Series,Artifact,Point
from wealth_manager.modules.macroeconomics.refresh import RefreshError

LIMIT=2_000_000
PATHS={'key_rate':'KeyRate','ofz_curve':'zcyc_params'}
SOURCE=Source('cbr_tables_v1','Банк России / Bank of Russia',url='https://www.cbr.ru/',
              terms='https://www.cbr.ru/user_agreement/; https://www.cbr.ru/About/?PrtId=use; checked 2026-11-21')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class Transport:
    def get(self,url):
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'PersonalWealthManager/1.0','Accept':'text/html'})
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as response:
                if response.status!=200 or response.headers.get_content_type()!='text/html':raise ValueError()
                body=response.read(LIMIT+1)
            if not body or len(body)>LIMIT:raise ValueError()
            return body
        except (OSError,ValueError) as error:
            raise RefreshError('Не удалось получить данные ЦБ. Сохранённые данные не изменены') from error

class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.tables=[];self.current=None;self.row=None;self.cell=None;self.inputs={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='input' and attrs.get('name') in ('UniDbQuery.From','UniDbQuery.To'):self.inputs[attrs['name']]=attrs.get('value')
        if tag=='table':
            if self.current is not None:raise ValueError('Nested table')
            self.current=[]
        elif tag=='tr' and self.current is not None:self.row=[]
        elif tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()));self.cell=None
        elif tag=='tr' and self.row is not None:
            self.current.append(self.row);self.row=None
        elif tag=='table' and self.current is not None:
            self.tables.append(self.current);self.current=None

def value(text):
    result=Decimal(text.replace(',','.'))
    if not result.is_finite() or not Decimal('-100')<=result<=Decimal('1000'):raise ValueError('Invalid yield/rate')
    return result

class Provider:
    def __init__(self,http=None):self.http=http or Transport()
    def fetch(self,kind,start,end):
        args={'UniDbQuery.Posted':'True','UniDbQuery.From':start.strftime('%d.%m.%Y'),'UniDbQuery.To':end.strftime('%d.%m.%Y')}
        url='https://www.cbr.ru/hd_base/'+PATHS[kind]+'/?'+urlencode(args)
        raw=self.http.get(url)
        try:
            if not isinstance(raw,bytes) or not 0<len(raw)<=LIMIT:raise ValueError('Size')
            parser=Tables();parser.feed(raw.decode('utf-8-sig'));parser.close()
            if parser.current is not None or any(parser.inputs.get(k)!=v for k,v in args.items() if k!='UniDbQuery.Posted'):raise ValueError('Range not acknowledged')
            matching=[t for t in parser.tables if t and t[0] and t[0][0]=='Дата']
            if len(matching)!=1:raise ValueError('Missing/ambiguous table')
            table=matching[0]
            if kind=='key_rate':
                if table[0]!=['Дата','Ставка']:raise ValueError('Header')
                specs=[('key_rate','Ключевая ставка, %','Key rate, %',1)]
                width=2;rows=table[1:]
            else:
                if table[0]!=['Дата','Срок до погашения, лет'] or len(table)<2:raise ValueError('Curve header')
                terms=[Decimal(x.replace(',','.')) for x in table[1]]
                if len(set(terms))!=len(terms):raise ValueError('Duplicate tenor')
                specs=[('ofz_zero_'+str(y)+'y','Бескупонная доходность ОФЗ '+str(y)+' лет, %','OFZ zero-coupon yield '+str(y)+' years, %',terms.index(Decimal(y))+1) for y in (1,2,5,10)]
                width=len(terms)+1;rows=table[2:]
            points={s[0]:[] for s in specs};seen=set()
            for row in rows:
                if len(row)!=width:raise ValueError('Row width')
                day=datetime.strptime(row[0],'%d.%m.%Y').date()
                if not start<=day<=end or day in seen:raise ValueError('Date')
                seen.add(day)
                for code,ru,en,column in specs:
                    if row[column] in ('','—','–','-'):continue
                    number=value(row[column])
                    if kind=='key_rate' and number<0:raise ValueError('Negative key rate')
                    points[code].append(Point(day.isoformat(),day.isoformat(),number))
            artifact=Artifact(url,raw,'cbr-html-v1','text/html')
            return [(Indicator(code,ru,en),SOURCE,Series(code,'RU',code,'percent','daily',adjustment='nsa',methodology='cbr_hd_base_'+PATHS[kind]+'_v1'),artifact,points[code]) for code,ru,en,col in specs if points[code]]
        except (ValueError,TypeError,IndexError,InvalidOperation,UnicodeError) as error:
            raise RefreshError('Некорректный ответ ЦБ') from error
