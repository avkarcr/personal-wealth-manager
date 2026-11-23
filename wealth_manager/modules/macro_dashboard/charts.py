"""Dated, exact chart data; rendering and approximate coordinates belong to the web."""
from datetime import date
from decimal import Decimal as D, localcontext
from wealth_manager.modules.macroeconomics.derived import calculate, options, value, period_number, validate_rows

CHARTS = dict(gdp='Реальный ВВП / годовой рост', gap='Разрыв выпуска', cpi='Инфляция год к году', key_rate='Ключевая ставка',
              curve='Кривая ОФЗ', spread='Спред ОФЗ 10 лет — 2 года', equities='Индекс акций', bonds='Индекс облигаций', money='RUSFAR', phases='История фаз цикла')
CHART_ROLES = dict(gdp=['gdp'],gap=['gdp'],cpi=['cpi'],key_rate=['key_rate'],curve=['yield_1y','yield_2y','yield_10y'],
                   spread=['yield_10y','yield_2y'],equities=['equities'],bonds=['bonds'],money=['money'])
UNITS = dict(percent='%',pp='п.п.',RUB='RUB',index='пункты индекса')


def point(row):
    return dict(date=row['period_end'],value=value(row),revisions=[row['revision_id']],note='')


def quality(data):
    if not data: return 'short'
    validate_rows(data)
    if any(r['methodology'] in ('unknown','legacy_unknown') or r['adjustment']=='unknown' for r in data):return 'metadata'
    return ''


def aligned(first,second):
    return all(first[k]==second[k] for k in ('country','source_id','methodology','frequency','unit','adjustment'))


def with_breaks(points,frequency):
    """Mark gaps explicitly; the browser must not join them or replace them by zero."""
    previous=None
    for p in points:
        p=dict(p,break_before=False)
        if previous:
            a,b=date.fromisoformat(previous['date']),date.fromisoformat(p['date'])
            if frequency=='daily':gap=(b-a).days>7
            else:
                gap=period_number(dict(period_start=p['date'],frequency=frequency))-period_number(dict(period_start=previous['date'],frequency=frequency))>1
            p['break_before']=gap
        previous=p
        yield p


def history(rows,cfg,cutoff,mode,kind,start):
    """One selected series per role. Lines use the vintage selected at cutoff."""
    with localcontext() as ctx:
        ctx.prec=50
        return _history(rows,cfg,cutoff,mode,kind,start)


def _history(rows,cfg,cutoff,mode,kind,start):
    selected={role:sorted((r for r in rows if r['series_id']==cfg['series'][role]),key=lambda r:r['period_end']) for role in CHART_ROLES[kind]}
    first=next(iter(selected.values())); points=[];reason=quality(first);unit=first[0]['unit'] if first else ''
    frequency=first[0]['frequency'] if first else 'daily';label=CHARTS[kind]
    if kind in ('gap','cpi') and not reason:
        output=calculate(first,[],options(cfg['method'],cfg['window'],cfg['hold_rate']),cutoff,mode)
        metric='gap' if kind=='gap' else 'yoy' if '_mom_' in first[0]['indicator'] else 'growth'
        m=next((m for m in output['metrics'] if m['code']==metric),{})
        points=m.get('points',[]);reason=m.get('reason') if not points else ''
        reason=reason or (output['reason'] or 'metadata' if not points else '');unit='percent'
    elif kind=='curve':
        # Shape at the latest selected observation date, never mix dates/providers.
        available=[r for data in selected.values() for r in data]
        reference=(selected['yield_10y'] or available)[-1] if available else None
        latest=max((r['period_end'] for r in available),default=''); unit='percent';reason='' if reference else 'short'
        for role,tenor in (('yield_1y',1),('yield_2y',2),('yield_10y',10)):
            data=selected[role];r=next((r for r in reversed(data) if r['period_end']==latest),None)
            valid=r is not None and not quality(data) and aligned(reference,r)
            points.append(dict(date=latest,x=tenor,value=value(r) if valid else None,revisions=[r['revision_id']] if valid else [],note='',reason='' if valid else 'no_overlap' if r is None else 'metadata',break_before=not valid))
        if not any(p['value'] is not None for p in points):reason=reason or 'no_overlap'
    elif kind=='spread':
        other=selected['yield_2y']; reason=reason or quality(other);unit='pp'
        if not reason and not aligned(first[0],other[0]):reason='metadata'
        if not reason:
            lookup={r['period_end']:r for r in other}
            points=[dict(date=r['period_end'],value=value(r)-value(lookup[r['period_end']]),revisions=[r['revision_id'],lookup[r['period_end']]['revision_id']],note='') for r in first if r['period_end'] in lookup]
            if not points:reason='no_overlap'
    elif not reason:
        if (kind=='gdp' and unit=='RUB' and first[0]['base_year'] is None):reason='metadata'
        elif any(value(r)<=0 for r in first) and unit in ('RUB','index'):reason='nonpositive'
        elif any(value(r)<=-100 for r in first) and unit=='percent':reason='nonpositive'
        points=[] if reason else [point(r) for r in first]
        if kind=='gdp' and first[0]['indicator'] not in ('gdp_real_level','gdp_real_level_sa'):
            points=[dict(p,value=p['value']-(100 if first[0]['indicator']=='gdp_volume_yoy_index' else 0)) for p in points]
            label='ВВП: рост год к году';unit='percent'
    if kind!='curve':points=list(with_breaks([p for p in points if start<=p['date']<=cutoff[:10]],frequency))
    if not points and not reason:reason='range'
    return dict(kind=kind,label=label,unit=unit,points=points,reason=reason,axis='tenor' if kind=='curve' else 'date',
                start=start,end=cutoff[:10],series=[dict(role=role,**{k:data[0][k] for k in ('series_id','source_title','indicator','unit','scale','base_year','adjustment','methodology','frequency')}) for role,data in selected.items() if data])
