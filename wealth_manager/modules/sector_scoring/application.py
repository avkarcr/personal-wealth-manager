"""Independent configuration, dated previews and immutable calculation evidence."""
from wealth_manager.modules.economic_cycle.application import Cycle, digest
from wealth_manager.modules.macroeconomics.data import utcnow, instant, DataReports
from wealth_manager.modules.economic_cycle.signals import prepare
from wealth_manager.modules.market_catalog.domain import find
from wealth_manager.shared.records import RecordError
from .config import defaults, validate, rule_for, VERSION
from .model import assess, AUTO_FEATURES
from .ports import Session

CHANGED = 'Отраслевые данные или настройки изменились: обновите страницу'


class Scoring:
    def __init__(self, session: Session, clock=utcnow):
        self.session, self.clock = session, clock

    def settings(self, token):
        snapshot=self.session.sector_snapshot(token)
        cfg=validate(snapshot['config'])
        return dict(config=cfg,config_id=snapshot['config_id'],sectors=snapshot['sectors'],
                    rules={s['id']:rule_for(s,snapshot['sectors'],cfg) for s in snapshot['sectors']})

    def configure(self, token, config, expected_id, restore=False):
        with self.session.write(token) as uow:
            repo=uow.sector_scoring
            if type(expected_id)!=int or repo.state()['config_id']!=expected_id: raise RecordError(CHANGED)
            cfg=validate(defaults() if restore else config,repo.sectors())
            cid=repo.configure(cfg,instant(self.clock()));uow.commit()
            return cid

    def observe(self, token, observation, expected_revision=None):
        now=instant(self.clock());payload=observation.validated(now)
        with self.session.write(token) as uow:
            repo=uow.sector_scoring
            if expected_revision is not None and (type(expected_revision)!=int or expected_revision!=repo.revision_token()): raise RecordError(CHANGED)
            sectors=repo.sectors();sector=find(sectors,payload['sector_id'])
            parent=find(sectors,sector['parent_id']) if sector['parent_id'] else None
            if sector['archived'] or (parent and parent['archived']): raise RecordError('Сначала восстановите отрасль из архива')
            rid=repo.observe(payload,now);uow.commit()
            return rid

    def preview(self, token, as_of=None, mode='known'):
        snapshot=self.session.sector_snapshot(token)
        cycle=Cycle(self.session,self.clock).from_snapshot(snapshot['cycle'],as_of,mode)['result']
        cfg=validate(snapshot['config'])
        class Reader:
            def read(self): return snapshot['cycle']['rows']
        rows=DataReports(Reader(),self.clock).overview(cycle['as_of'],mode)['rows']
        # Share series selection and transformations, never the cycle's factor weights.
        evidence,_=prepare(rows,cycle['config'],cycle['as_of'],mode,include_disabled=True)
        macro_evidence={code:evidence[code] for code in AUTO_FEATURES.values()}
        result=assess(snapshot['sectors'],snapshot['observations'],dict(cycle,features=macro_evidence),cfg,cycle['as_of'],mode)
        result.update(version=VERSION,config=cfg,config_version=digest(cfg),cycle=cycle,macro_evidence=macro_evidence,as_of=cycle['as_of'],mode=mode)
        return dict(result=result,fingerprint=digest(result),config_id=snapshot['config_id'],
                    revision_token=snapshot['revision_token'],macro_token=snapshot['cycle']['revision_token'],
                    cycle_config_id=snapshot['cycle']['config_id'],catalog_token=digest(snapshot['sectors']),runs=snapshot['runs'])

    def save(self, token, expected_fingerprint=None, **parameters):
        preview=self.preview(token,**parameters)
        if expected_fingerprint is not None and expected_fingerprint!=preview['fingerprint']: raise RecordError(CHANGED)
        with self.session.write(token) as uow:
            repo=uow.sector_scoring
            if (repo.state()['config_id']!=preview['config_id'] or repo.revision_token()!=preview['revision_token']
                    or uow.macro_data.revision_token()!=preview['macro_token'] or uow.economic_cycle.state()['config_id']!=preview['cycle_config_id']
                    or digest(repo.sectors())!=preview['catalog_token']): raise RecordError(CHANGED)
            rid=repo.save(preview['fingerprint'],preview['result'],instant(self.clock()));uow.commit()
            return rid

    def saved(self, token, rid):
        return self.session.saved_sector(token,rid)
