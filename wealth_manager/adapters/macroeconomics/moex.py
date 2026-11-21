"""Bounded ISS history, explicit board/session and checked pagination."""
import json
from decimal import Decimal, InvalidOperation
from datetime import date
from urllib.parse import urlencode
import urllib.request
from .cbr import NoRedirect
from wealth_manager.modules.macroeconomics.data import Indicator,Source,Series,Artifact,Point
from wealth_manager.modules.macroeconomics.refresh import RefreshError

MARKETS={
 'IMOEX':('SNDX','Индекс МосБиржи: ценовой','MOEX Russia price index','index'),
 'MCFTR':('RTSI','Индекс МосБиржи: полная доходность брутто','MOEX Russia gross total return','index'),
 'RGBI':('SNDX','Гособлигации: ценовой индекс RGBI','Government bond price index RGBI','index'),
 'RGBITR':('SNDX','Гособлигации: полная доходность RGBITR','Government bond total return RGBITR','index'),
 'RUSFAR':('MMIX','Ставка RUSFAR, %','RUSFAR rate, %','percent'),
}
SOURCE=Source('moex_iss_history_v1','Московская биржа / Moscow Exchange',url='https://www.moex.com/ru/indices',terms='https://www.moex.com/a2193; familiarization only unless covered by an agreement; checked 2026-11-22')

class Transport:
    def get(self,url):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'PersonalWealthManager/1.0','Accept':'application/json'})
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as response:
                if response.status!=200:raise ValueError()
                raw=response.read(2_000_001)
            if not 0<len(raw)<=2_000_000:raise ValueError()
            return raw
        except (OSError,ValueError) as error:raise RefreshError('Источник рынка недоступен') from error

def records(block):
    cols=block['columns'];rows=block['data']
    if len(cols)!=len(set(cols)) or any(len(row)!=len(cols) for row in rows):raise ValueError('Columns')
    return [dict(zip(cols,row)) for row in rows]

class Provider:
    def __init__(self,http=None):self.http=http or Transport()
    def fetch(self,kind,start,end,cancel=lambda:False):
        if kind not in MARKETS or not 0<=(end-start).days<=89:raise RefreshError('Рыночный запрос ограничен 90 днями')
        board,ru,en,unit=MARKETS[kind];offset=0;expected_total=None;seen=set();bundles=[]
        try:
            for _ in range(4):
                if cancel():raise RefreshError('Обновление отменено')
                url='https://iss.moex.com/iss/history/engines/stock/markets/index/securities/'+kind+'.json?'+urlencode({'from':start.isoformat(),'till':end.isoformat(),'iss.meta':'off','start':offset})
                raw=self.http.get(url);data=json.loads(raw,parse_float=Decimal)
                cursor=records(data['history.cursor'])
                if len(cursor)!=1:raise ValueError('Cursor')
                cur=cursor[0];total=cur['TOTAL'];size=cur['PAGESIZE']
                if type(total)!=int or type(size)!=int or not 0<=total<=400 or not 1<=size<=100 or cur['INDEX']!=offset:raise ValueError('Cursor bounds')
                if expected_total is not None and total!=expected_total:raise ValueError('Changed pagination')
                expected_total=total;rows=records(data['history'])
                if len(rows)!=min(size,total-offset):raise ValueError('Truncated page')
                points=[]
                for row in rows:
                    day=date.fromisoformat(row['TRADEDATE'])
                    if row['SECID']!=kind or not start<=day<=end:raise ValueError('Identity/date')
                    if row['BOARDID']!=board or str(row['TRADINGSESSION'])!='3':continue
                    if day in seen:raise ValueError('Duplicate date')
                    seen.add(day)
                    if row['CLOSE'] is None:continue
                    value=Decimal(str(row['CLOSE']))
                    if not value.is_finite() or value<=0:raise ValueError('Value')
                    points.append(Point(day.isoformat(),day.isoformat(),value))
                if points:
                    code='moex_'+kind.lower()
                    bundles.append((Indicator(code,ru,en),SOURCE,Series(code,'RU',kind,unit,'daily',adjustment='nsa',methodology='iss_close_'+board+'_session3_v1'),Artifact(url,raw,'moex-iss-v1','application/json'),points))
                offset+=len(rows)
                if offset>=total:return bundles
            raise ValueError('Page limit')
        except (KeyError,ValueError,TypeError,OverflowError,InvalidOperation) as error:
            if isinstance(error,RefreshError):raise
            raise RefreshError('Некорректная история рынка') from error
