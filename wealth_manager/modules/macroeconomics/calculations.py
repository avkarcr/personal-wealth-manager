"""Application use cases: inspect an as-of snapshot and persist its exact evidence."""
from hashlib import sha256
import json
from .data import DataReports, instant, utcnow
from .derived import VERSION, options, calculate, ERROR
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.decimal import exact_text


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=exact_text)


class Calculations:
    def __init__(self,session,clock=utcnow):
        self.session,self.clock=session,clock

    def preview(self,token,primary=None,secondary=None,as_of=None,mode='known',method='hp',window=40,hold_rate=False):
        cfg=options(method,window,hold_rate)
        if any(v is not None and (type(v)!=int or v<=0) for v in (primary,secondary)) or (primary is not None and primary==secondary): raise RecordError(ERROR)
        snapshot=self.session.calculation_snapshot(token)
        class SnapshotReader:
            def read(self):return snapshot['rows']
        now=instant(self.clock()); cutoff=instant(as_of,True) if as_of else now
        if cutoff[:10]>now[:10]: raise RecordError(ERROR)
        report=DataReports(SnapshotReader(),self.clock).overview(cutoff,mode)
        catalog={r['series_id']:r for r in snapshot['rows']}
        if any(sid is not None and sid not in catalog for sid in (primary,secondary)): raise RecordError(ERROR)
        rows=[r for r in report['rows'] if r['series_id']==primary]
        other=[r for r in report['rows'] if r['series_id']==secondary]
        result=calculate(rows,other,cfg,cutoff,mode)
        inputs=sorted({r['revision_id'] for r in rows+other})
        config=dict(primary=primary,secondary=secondary,**cfg)
        result.update(version=VERSION,as_of=cutoff,mode=mode,config=config,inputs=inputs,
                      # These snapshots make the result reviewable even when source values are revised.
                      series=[{k:catalog[sid][k] for k in ('series_id','indicator','country','source_id','source_title','source_kind','unit','scale','frequency','adjustment','base_year','methodology')} for sid in (primary,secondary) if sid is not None])
        body=canonical(result); fingerprint=sha256(body.encode()).hexdigest()
        return dict(result=result,fingerprint=fingerprint,revision_token=snapshot['revision_token'],
                    choices=sorted(catalog.values(),key=lambda r:(r['indicator'],r['series_id'])),runs=snapshot['runs'])

    def save(self,token,expected_fingerprint=None,**parameters):
        preview=self.preview(token,**parameters); result=preview['result']
        if expected_fingerprint is not None and expected_fingerprint!=preview['fingerprint']: raise RecordError('Результат изменился: повторите предпросмотр')
        if not any(m['points'] for m in result['metrics']): raise RecordError('Нет рассчитанных значений для сохранения')
        with self.session.write(token) as uow:
            if uow.macro_data.revision_token()!=preview['revision_token']: raise RecordError('Макроданные изменились: повторите расчёт')
            rid=uow.macro_calculations.save(preview['fingerprint'],result,instant(self.clock()))
            uow.commit()
        return rid

    def saved(self,token,rid):
        return self.session.saved_calculation(token,rid)
