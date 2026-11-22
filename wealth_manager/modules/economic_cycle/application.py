"""As-of use cases, immutable configuration versions and reproducible evidence."""
from datetime import date
from hashlib import sha256
from wealth_manager.modules.macroeconomics.data import DataReports, instant, utcnow
from wealth_manager.modules.macroeconomics.derived import minus_months, VERSION as DERIVED_VERSION
from wealth_manager.modules.macroeconomics.calculations import canonical
from wealth_manager.shared.records import RecordError
from .config import VERSION, ERROR, defaults, validate
from .signals import prepare
from .model import assess
from .ports import Session


def digest(value): return sha256(canonical(value).encode()).hexdigest()


class Cycle:
    def __init__(self, session: Session, clock=utcnow): self.session,self.clock=session,clock

    def settings(self,token):
        snapshot=self.session.cycle_snapshot(token)
        catalog={r['series_id']:r for r in snapshot['rows']}
        return dict(config=validate(snapshot['config']),config_id=snapshot['config_id'],choices=sorted(catalog.values(),key=lambda r:(r['indicator'],r['series_id'])))

    def configure(self,token,config,expected_id,restore=False):
        snapshot=self.session.cycle_snapshot(token)
        if type(expected_id)!=int or expected_id!=snapshot['config_id']: raise RecordError('Настройки цикла изменились: откройте форму заново')
        if restore: config=dict(defaults(),series=snapshot['config']['series'])
        config=validate(config,{r['series_id']:r for r in snapshot['rows']})
        with self.session.write(token) as uow:
            if uow.economic_cycle.state()['config_id']!=expected_id: raise RecordError('Настройки цикла изменились: откройте форму заново')
            cid=uow.economic_cycle.configure(config,instant(self.clock()))
            uow.commit()
        return cid

    def preview(self,token,as_of=None,mode='known'):
        snapshot=self.session.cycle_snapshot(token); cfg=validate(snapshot['config'])
        now=instant(self.clock()); cutoff=instant(as_of,True) if as_of else now
        if as_of and len(as_of)==10 and cutoff[:10]==now[:10]: cutoff=now
        if cutoff>now: raise RecordError(ERROR)
        previous=minus_months(date.fromisoformat(cutoff[:10]),3).isoformat()+cutoff[10:]
        class Reader:
            def read(self): return snapshot['rows']
        reports=DataReports(Reader(),self.clock)
        current_rows=reports.overview(cutoff,mode)['rows']; previous_rows=reports.overview(previous,mode)['rows']
        evidence,warnings=prepare(current_rows,cfg,cutoff,mode)
        prior,prior_warnings=prepare(previous_rows,cfg,previous,mode)
        result=assess(evidence,prior,cfg)
        selected=[r for r in current_rows+previous_rows if r['series_id'] in cfg['series'].values()]
        catalog={r['series_id']:r for r in snapshot['rows']}
        series={role:{k:catalog[sid][k] for k in ('series_id','indicator','country','source_id','source_title','unit','frequency','scale','adjustment','base_year','methodology')} for role,sid in cfg['series'].items() if sid in catalog}
        result.update(version=VERSION,derived_version=DERIVED_VERSION,config=cfg,config_version=digest(cfg),as_of=cutoff,mode=mode,
                      previous_as_of=previous,previous_features=prior,warnings=warnings,previous_warnings=prior_warnings,
                      inputs=sorted({r['revision_id'] for r in selected}),series=series)
        return dict(result=result,fingerprint=digest(result),revision_token=snapshot['revision_token'],config_id=snapshot['config_id'],runs=snapshot['runs'])

    def save(self,token,expected_fingerprint=None,**parameters):
        preview=self.preview(token,**parameters)
        if expected_fingerprint is not None and expected_fingerprint!=preview['fingerprint']: raise RecordError('Результат изменился: повторите предпросмотр')
        with self.session.write(token) as uow:
            if uow.macro_data.revision_token()!=preview['revision_token']: raise RecordError('Макроданные изменились: повторите расчёт')
            if uow.economic_cycle.state()['config_id']!=preview['config_id']: raise RecordError('Настройки цикла изменились: откройте форму заново')
            rid=uow.economic_cycle.save(preview['fingerprint'],preview['result'],instant(self.clock()))
            uow.commit()
        return rid

    def saved(self,token,rid): return self.session.saved_cycle(token,rid)
