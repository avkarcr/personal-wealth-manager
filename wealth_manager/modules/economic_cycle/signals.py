"""Prepare dated evidence from M6 transformations; absent/stale is never zero."""
from datetime import date
from decimal import Decimal as D, localcontext, ROUND_HALF_EVEN
from wealth_manager.modules.macroeconomics.derived import calculate, options, calendar_changes, period_number, value
from .config import FEATURES

REASONS = dict(short='Недостаточно непрерывных наблюдений для расчёта', stale='Последнее наблюдение устарело для выбранной даты',
               metadata='Проверьте частоту, единицы, сезонность, базовый год и методику ряда',
               lag='Последняя точка расчёта отстаёт от выбранного ряда', disabled='Признак отключён',
               missing_pair='Выберите сопоставимый второй ряд', no_overlap='Нет сопоставимых дат для двух рядов',
               nonpositive='Для расчёта нужны положительные уровни или индексы',
               assumption='Для ключевой ставки подтвердите допущение: она остаётся неизменной следующие 12 месяцев',
               expectations='Ожидания должны относиться к следующим 12 месяцам; фактическая инфляция не подходит',
               unsupported='Для этого показателя расчёт не предусмотрен')


def prepare(rows, cfg, cutoff, mode, *, include_disabled=False):
    with localcontext() as ctx:
        ctx.prec=50; ctx.rounding=ROUND_HALF_EVEN
        return _prepare(rows,cfg,cutoff,mode,include_disabled)


def _prepare(rows, cfg, cutoff, mode, include_disabled=False):
    bound = {sid: [] for sid in cfg['series'].values() if sid is not None}
    for row in rows:
        if row['series_id'] in bound:
            bound[row['series_id']].append(row)
    # Limit the history per bound series before building all derived points.
    limits = {'daily': 110, 'monthly': 14, 'quarterly': cfg['window']}
    for sid, data in bound.items():
        if data:
            data.sort(key=lambda row: row['period_end'])
            bound[sid] = data[-limits[data[0]['frequency']]:]
    groups = {role: bound.get(sid, []) for role, sid in cfg['series'].items()}
    opts = options(cfg['method'],cfg['window'],cfg['hold_rate'])
    cache, warnings, evidence = {}, set(), {}
    def derived(role, other=''):
        key=(role,other)
        if key not in cache:
            output=calculate(groups[role],groups[other] if other else [],opts,cutoff,mode)
            warnings.update(output['warnings'])
            cache[key]=({m['code']:m for m in output['metrics']},output['reason'])
        return cache[key]
    def take(feature, roles, points, reason='short', estimate=False):
        selected=[r for role in roles for r in groups[role]]
        if not selected: reason='short'
        elif any(r['indicator'] in ('key_rate','moex_rusfar','inflation_expectations_12m') and value(r)<=-100 for r in selected): reason='metadata'; points=[]
        elif any(r['methodology'] in ('unknown','legacy_unknown') or r['adjustment']=='unknown' for r in selected): reason='metadata'; points=[]
        if points and selected:
            last=max(r['period_end'] for r in selected)
            if not estimate and points[-1]['date']!=last: reason='lag'; points=[]
            elif any((date.fromisoformat(cutoff[:10])-date.fromisoformat(groups[role][-1]['period_end'])).days>{'daily':7,'monthly':75,'quarterly':200}[groups[role][-1]['frequency']] for role in roles if groups[role]): reason='stale'; points=[]
        evidence[feature]=dict(value=points[-1]['value'] if points else None, date=points[-1]['date'] if points else None,
                               revisions=points[-1]['revisions'] if points else [], reason='' if points else reason,
                               roles=roles, note=points[-1].get('note','') if points else '')
    def metric(feature,role,code,other='',estimate=False):
        metrics,reason=derived(role,other); m=metrics.get(code,{})
        take(feature,[role]+([other] if other else []),m.get('points',[]),m.get('reason') or reason or 'short',estimate)
    def raw(role):
        return [dict(date=r['period_end'],value=value(r),revisions=[r['revision_id']]) for r in groups[role]]
    def change(feature,role,negate=False):
        data=groups[role]
        points=[]
        if data and data[0]['frequency']=='monthly':
            # Month-end dates vary: compare period numbers, not Jan 30 vs Jan 31.
            periods={period_number(r):r for r in data}
            for r in data:
                n=period_number(r)
                if all(n-j in periods for j in range(4)):
                    points.append(dict(date=r['period_end'],value=value(r)-value(periods[n-3]),revisions=sorted(periods[n-j]['revision_id'] for j in range(4))))
        elif data: points=calendar_changes(data,(3,))[0]['points']
        if negate: points=[dict(p,value=-p['value']) for p in points]
        take(feature,[role],points)
    gdp=groups['gdp']
    metric('gdp_yoy','gdp','yoy' if gdp and gdp[0]['indicator'] in ('gdp_real_level','gdp_real_level_sa') else 'growth')
    metric('gdp_qoq','gdp_qoq','growth') if cfg['series']['gdp_qoq'] else metric('gdp_qoq','gdp','qoq')
    if evidence['gdp_yoy']['value'] is not None and evidence['gdp_qoq']['value'] is not None and evidence['gdp_yoy']['date']!=evidence['gdp_qoq']['date']:
        take('gdp_qoq',['gdp_qoq'],[],'lag')
    metric('output_gap','gdp','gap'); metric('gdp_accel','gdp','acceleration'); metric('industry','industry','growth')
    change('employment','unemployment',True)
    if any(not 0<=value(r)<=100 for r in groups['unemployment']): take('employment',['unemployment'],[],'metadata')
    cpi=groups['cpi']; code='yoy' if cpi and '_mom_' in cpi[0]['indicator'] else 'growth'
    metric('cpi_yoy','cpi',code)
    metrics,_=derived('cpi'); points=metrics.get(code,{}).get('points',[])
    # Acceleration of YoY, never the MoM acceleration returned for a MoM source.
    lookup={r['period_end']:r for r in cpi}; accel=[]
    for a,b in zip(points,points[1:]):
        if period_number(lookup[b['date']])-period_number(lookup[a['date']])==1:
            accel.append(dict(date=b['date'],value=b['value']-a['value'],revisions=sorted(set(a['revisions']+b['revisions']))))
    take('cpi_accel',['cpi'],accel)
    metric('cpi_saar','cpi_sa','saar'); metric('core_yoy','core','yoy'); change('expectations','expectations')
    change('key_change','key_rate'); take('key_level',['key_rate'],raw('key_rate'))
    metric('real_rate',cfg['real_rate_basis'],'real_rate','expectations',True)
    metric('spread_1','yield_10y','spread','yield_1y'); metric('spread_2','yield_10y','spread','yield_2y')
    metric('curve_change','yield_10y','spread_3m','yield_2y')
    metric('equities','equities','return_3m'); metric('bonds','bonds','return_3m'); change('money','money')
    for feature in FEATURES:
        f=cfg['features'][feature]
        if not include_disabled and (not f['enabled'] or not D(f['weight']) or not D(cfg['groups'][FEATURES[feature][0]])):
            evidence[feature].update(value=None,date=None,revisions=[],reason='disabled',note='')
    selected=[r for r in rows if r['series_id'] in cfg['series'].values()]
    if any(r['publication_precision']=='unknown' for r in selected): warnings.add('unknown_publication')
    if any(r['status']=='revised' for r in selected): warnings.add('revised')
    if mode=='latest': warnings.add('latest')
    return evidence,sorted(warnings)
