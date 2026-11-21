"""Persistent scheduling policy, guarded refresh and atomic checkpoints."""
from datetime import date,datetime,timedelta
from threading import Lock
from uuid import uuid4
from .refresh import RefreshError
from .data import instant,utcnow
from wealth_manager.shared.records import iso_date

KINDS=('key_rate','ofz_curve','IMOEX','MCFTR','RGBI','RGBITR','RUSFAR')
MINIMUM={'key_rate':'2013-09-17','ofz_curve':'2003-01-04',**{k:'1990-01-01' for k in KINDS[2:]}}

def defaults():return dict(schedules={},runs=[],access='disabled',version=1)

def later(now,seconds):return (datetime.fromisoformat(now)+timedelta(seconds=seconds)).isoformat(timespec='microseconds')

class Scheduler:
    def __init__(self,provider,session,clock=utcnow):
        self.provider,self.session,self.clock=provider,session,clock
        self.gate=Lock()
    def state(self,token):
        return self.session.read_state(token)
    def configure(self,kind,enabled,start,hours,access,token):
        now=instant(self.clock());iso_date(start)
        if kind not in KINDS or start<MINIMUM[kind] or start>now[:10] or type(hours)!=int or not 1<=hours<=168 or access not in ('disabled','familiarization','agreement'):raise RefreshError('Некорректные настройки обновления')
        if kind in KINDS[2:] and enabled and access=='disabled':raise RefreshError('Выберите режим доступа MOEX')
        with self.session.write(token) as uow:
            state=uow.macro_data.refresh_state();old=state['schedules'].get(kind,{})
            state['access']=access
            state['schedules'][kind]=dict(enabled=bool(enabled),start=start,hours=hours,checkpoint=old.get('checkpoint','') if old.get('start')==start else '',next_due=now,failures=0,version=old.get('version',0)+1)
            uow.macro_data.save_refresh_state(state);uow.commit()
    def run(self,kind,start,end,token,scheduled=False,cancel=lambda:False):
        iso_date(start);iso_date(end)
        first,last=date.fromisoformat(start),date.fromisoformat(end);now=instant(self.clock())
        limit=365 if kind in KINDS[:2] else 89
        if kind not in KINDS or start<MINIMUM[kind] or not 0<=(last-first).days<=limit or end>now[:10]:raise RefreshError('Некорректный диапазон обновления')
        if not self.gate.acquire(False):raise RefreshError('Обновление уже выполняется')
        run_id=None
        try:
            with self.session.write(token) as uow:
                state=uow.macro_data.refresh_state();cfg=state['schedules'].get(kind,{})
                if kind in KINDS[2:] and state['access']=='disabled':raise RefreshError('Выберите режим доступа MOEX')
                if scheduled and not cfg.get('enabled'):return 0
                version=cfg.get('version',0);access=state['access'];run_id=uuid4().hex
                # Holding gate proves no run in this process is still executing.
                for run in state['runs']:
                    if run['status']=='running':run['status']='interrupted';run['finished']=now
                state['runs']=(state['runs']+[dict(id=run_id,kind=kind,start=start,end=end,started=now,finished='',status='running',count=0,access=access)])[-100:]
                uow.macro_data.save_refresh_state(state);uow.commit()
            if cancel():raise RefreshError('Обновление отменено')
            bundles=self.provider.fetch(kind,first,last,cancel)
            seen=set();count=0
            for indicator,source,series,artifact,points in bundles:
                indicator.validate();source.validate();series.validate();artifact.validate()
                if indicator.code!=series.indicator:raise RefreshError('Некорректный пакет обновления')
                for p in points:
                    p.validate(series);key=(series,p.period_start,p.period_end)
                    if key in seen or p.period_start<start or p.period_end>end:raise RefreshError('Некорректный пакет обновления')
                    seen.add(key);count+=1
            if count>10000:raise RefreshError('Некорректный пакет обновления')
            with self.session.write(token) as uow:
                state=uow.macro_data.refresh_state();current=state['schedules'].get(kind,{})
                if cancel() or current.get('version',0)!=version or (kind in KINDS[2:] and state['access']!=access):raise RefreshError('Настройки изменились: ответ не сохранён')
                for bundle in bundles:uow.macro_data.ingest(*bundle)
                finished=instant(self.clock())
                for run in state['runs']:
                    if run['id']==run_id:run.update(status='success' if count else 'empty',finished=finished,count=count)
                if scheduled:
                    current.update(checkpoint=end,failures=0,next_due=later(finished,60 if end<(date.fromisoformat(finished[:10])-timedelta(days=1)).isoformat() else current['hours']*3600))
                uow.macro_data.save_refresh_state(state);uow.commit()
            return count
        except Exception:
            if run_id:
                try:
                    with self.session.write(token) as uow:
                        state=uow.macro_data.refresh_state();finished=instant(self.clock())
                        for run in state['runs']:
                            if run['id']==run_id:run.update(status='failed',finished=finished)
                        cfg=state['schedules'].get(kind,{})
                        if scheduled and cfg.get('version',0)==version:
                            failures=min(cfg.get('failures',0)+1,10);cfg.update(failures=failures,next_due=later(finished,min(300*2**(failures-1),21600)))
                        uow.macro_data.save_refresh_state(state);uow.commit()
                except Exception:pass # Locked or failed disk: original durable running entry is recovered next time.
            raise
        finally:self.gate.release()
    def execute(self,kind,start,end,token):return self.run(kind,start,end,token)
    def tick(self,cancel=lambda:False):
        token=self.session.current()
        if not token or cancel() or self.gate.locked():return
        state=self.state(token);now=instant(self.clock());yesterday=date.fromisoformat(now[:10])-timedelta(days=1)
        due=sorted(state['schedules'].items(),key=lambda item:item[1]['next_due'])
        for kind,cfg in due:
            if not cfg['enabled'] or cfg['next_due']>now or (kind in KINDS[2:] and state['access']=='disabled'):continue
            start=date.fromisoformat(cfg['start']);checkpoint=cfg.get('checkpoint')
            if checkpoint:start=max(start,date.fromisoformat(checkpoint)-timedelta(days=6))
            if start>yesterday:continue
            end=min(yesterday,start+timedelta(days=89))
            self.run(kind,start.isoformat(),end.isoformat(),token,True,cancel)
            return
