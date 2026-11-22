"""Pure, versioned transformations of explicitly selected comparable series."""
from bisect import bisect_right
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from wealth_manager.shared.records import RecordError
from .data import Point, Series

D = Decimal
VERSION = 'macro-derived-v1'
ERROR = 'Некорректные параметры расчёта макропоказателей'
LABELS = {
    'growth': 'Темп роста', 'yoy': 'Рост год к году', 'qoq': 'Рост квартал к кварталу SA',
    'acceleration': 'Изменение темпа роста', 'saar': 'Инфляция 3M SAAR',
    'change_bp': 'Изменение ставки, б.п.', 'spread': 'Спред доходности',
    'spread_3m': 'Изменение спреда за 3 месяца', 'spread_6m': 'Изменение спреда за 6 месяцев',
    'spread_12m': 'Изменение спреда за 12 месяцев',
    'return_1m': 'Доходность за 1 месяц', 'return_3m': 'Доходность за 3 месяца',
    'return_6m': 'Доходность за 6 месяцев', 'return_12m': 'Доходность за 12 месяцев',
    'gap': 'Output Gap — оценка модели', 'potential': 'Тренд реального ВВП — оценка модели',
    'sensitivity': 'Разница оценок HP и логлинейного тренда', 'real_rate': 'Реальная ставка ex ante',
}
MESSAGES = {
    'short': 'Недостаточно непрерывных наблюдений для расчёта',
    'metadata': 'Проверьте частоту, единицы, сезонность, базовый год и методику ряда',
    'unsupported': 'Для этого показателя расчёт не предусмотрен',
    'missing_pair': 'Выберите сопоставимый второй ряд',
    'no_overlap': 'Нет сопоставимых дат для двух рядов',
    'nonpositive': 'Для расчёта нужны положительные уровни или индексы',
    'gaps': 'Есть пропуски периодов: расчёты через разрыв не выполняются',
    'unknown_publication': 'Есть неизвестные даты публикации; доступность определяется моментом получения',
    'revised': 'В расчёте используются пересмотренные данные',
    'stale': 'Последнее наблюдение устарело для выбранной даты',
    'model': 'Оценка тренда не является официальным потенциальным ВВП. Конец ряда чувствителен к методу и новым данным',
    'assumption': 'Для ключевой ставки подтвердите допущение: она остаётся неизменной следующие 12 месяцев',
    'expectations': 'Ожидания должны относиться к следующим 12 месяцам; фактическая инфляция не подходит',
    'latest': 'Это пересчитанная история, а не сведения, известные тогда.',
}
INDEX_GROWTH = {'cpi_mom_index', 'cpi_yoy_index', 'core_cpi_mom_index',
                'cpi_mom_sa_index', 'gdp_volume_yoy_index', 'industrial_yoy_index'}
PERCENT_GROWTH = {'cpi_mom_growth', 'cpi_yoy_growth', 'cpi_mom_sa_growth',
                  'gdp_real_yoy_growth', 'gdp_real_qoq_sa_growth'}
MOM = {'cpi_mom_index', 'cpi_mom_growth', 'core_cpi_mom_index', 'cpi_mom_sa_index', 'cpi_mom_sa_growth'}
MARKETS = {'moex_imoex', 'moex_mcftr', 'moex_rgbi', 'moex_rgbitr'}
RATES = {'key_rate', 'ofz_zero_1y', 'ofz_zero_2y', 'ofz_zero_5y', 'ofz_zero_10y', 'moex_rusfar'}


def options(method='hp', window=40, hold_rate=False):
    if method not in ('hp', 'log_linear') or type(window) != int or not 20 <= window <= 120 or type(hold_rate) != bool:
        raise RecordError(ERROR)
    return dict(method=method, window=window, minimum=20, hp_lambda=1600,
                hold_rate=hold_rate, anchor_tolerance_days=7, expectations_max_age_days=45,
                flat_curve_threshold_pp='0.10')


def minus_months(value, months):
    n = value.year * 12 + value.month - 1 - months
    y, m = divmod(n, 12)
    return date(y, m + 1, min(value.day, monthrange(y, m + 1)[1]))


def period_number(row):
    d = date.fromisoformat(row['period_start'])
    return d.year if row['frequency'] == 'annual' else d.year * 4 + (d.month-1)//3 if row['frequency'] == 'quarterly' else d.year * 12 + d.month - 1


def value(row):
    return D(row['value']) * D(row['scale'])


def point(row, number, inputs, note=''):
    # Provenance belongs to the value, including every observation in a model window.
    return dict(date=row['period_end'], value=number, revisions=sorted({r['revision_id'] for r in inputs}), note=note)


def metric(code, unit, points, reason='short'):
    return dict(code=code, label=LABELS[code], unit=unit, points=points,
                status='OK' if points else 'INSUFFICIENT_DATA', reason='' if points else reason)


def hp_trend(values, smoothing=D(1600)):
    """Solve (I + lambda D'D)t = y by banded LDL'; O(n) storage/time."""
    n = len(values)
    if n < 3 or smoothing <= 0: raise RecordError(ERROR)
    diagonal, off1, off2 = [D(1)]*n, [D(0)]*n, [D(0)]*n
    for i in range(n-2):
        diagonal[i] += smoothing
        diagonal[i+1] += 4*smoothing
        diagonal[i+2] += smoothing
        off1[i+1] -= 2*smoothing
        off1[i+2] -= 2*smoothing
        off2[i+2] += smoothing
    l1, l2, pivots, forward = [D(0)]*n, [D(0)]*n, [D(0)]*n, [D(0)]*n
    for i in range(n):
        if i >= 2: l2[i] = off2[i] / pivots[i-2]
        if i >= 1: l1[i] = (off1[i] - (l2[i]*pivots[i-2]*l1[i-1] if i >= 2 else 0)) / pivots[i-1]
        pivots[i] = diagonal[i] - (l1[i]**2*pivots[i-1] if i else 0) - (l2[i]**2*pivots[i-2] if i >= 2 else 0)
        forward[i] = values[i] - (l1[i]*forward[i-1] if i else 0) - (l2[i]*forward[i-2] if i >= 2 else 0)
    result = [D(0)]*n
    for i in range(n-1, -1, -1):
        result[i] = forward[i]/pivots[i] - (l1[i+1]*result[i+1] if i+1 < n else 0) - (l2[i+2]*result[i+2] if i+2 < n else 0)
    return result


def log_linear_endpoint(logs):
    n = D(len(logs)); center = (n-1)/2; average = sum(logs)/n
    slope = sum((D(i)-center)*(y-average) for i,y in enumerate(logs)) / sum((D(i)-center)**2 for i in range(len(logs)))
    return average + slope*center


def regular_metrics(rows, normalized):
    changes = []
    for previous, current in zip(rows, rows[1:]):
        if period_number(current) - period_number(previous) == 1:
            changes.append(point(current, normalized[current['revision_id']]-normalized[previous['revision_id']], [previous,current]))
    return metric('acceleration', 'pp', changes)


def growth_metrics(rows):
    code = rows[0]['indicator']; normalized = {r['revision_id']: value(r)-(100 if code in INDEX_GROWTH else 0) for r in rows}
    result = [metric('growth', 'percent', [point(r,normalized[r['revision_id']],[r]) for r in rows]), regular_metrics(rows, normalized)]
    if code in MOM:
        yoy, saar = [], []
        for i, r in enumerate(rows):
            for width, target, annualize in ((12,yoy,1),(3,saar,4)):
                part = rows[max(0,i-width+1):i+1]
                if len(part)!=width or period_number(part[-1])-period_number(part[0])!=width-1: continue
                factor = D(1)
                for p in part: factor *= 1+normalized[p['revision_id']]/100
                target.append(point(r,(factor**annualize-1)*100,part))
        result.append(metric('yoy','percent',yoy))
        result.append(metric('saar','percent',saar if rows[0]['adjustment']=='sa' else [],'metadata' if rows[0]['adjustment']!='sa' else 'short'))
    return result


def gdp_metrics(rows, cfg):
    lookup = {period_number(r): r for r in rows}; yoy, qoq = [], []
    lag = 4 if rows[0]['frequency']=='quarterly' else 1
    for r in rows:
        for periods, target in ((lag,yoy),(1,qoq)):
            previous = lookup.get(period_number(r)-periods)
            if previous and all(period_number(r)-j in lookup for j in range(periods+1)): target.append(point(r,(value(r)/value(previous)-1)*100,[previous,r]))
    result = [metric('yoy','percent',yoy)]
    # Acceleration needs adjacent growth periods, not the adjacent available observations.
    growth_by_date = {p['date']:p for p in yoy}
    growth_rows = [dict(r,value=growth_by_date[r['period_end']]['value'],scale=D(1),growth_inputs=growth_by_date[r['period_end']]['revisions']) for r in rows if r['period_end'] in growth_by_date]
    accel = regular_metrics(growth_rows,{r['revision_id']:r['value'] for r in growth_rows})
    for p in accel['points']:
        p['revisions'] = sorted({rid for r in growth_rows if r['revision_id'] in p['revisions'] for rid in r['growth_inputs']})
    result.append(accel)
    compatible = rows[0]['frequency']=='quarterly' and rows[0]['adjustment']=='sa'
    result.append(metric('qoq','percent',qoq if compatible else [],'metadata' if not compatible else 'short'))
    gaps, potentials, sensitivity = [], [], []
    if compatible:
        logs_all = [value(r).ln() for r in rows]
        segment_start = 0
        for i,r in enumerate(rows):
            if i and period_number(r)-period_number(rows[i-1])!=1: segment_start=i
            part = rows[max(segment_start,i-cfg['window']+1):i+1]
            if len(part)<cfg['minimum'] or period_number(part[-1])-period_number(part[0]) != len(part)-1: continue
            logs = logs_all[max(segment_start,i-cfg['window']+1):i+1]
            hp, linear = hp_trend(logs, D(cfg['hp_lambda']))[-1], log_linear_endpoint(logs)
            trend = hp if cfg['method']=='hp' else linear
            gap_hp, gap_linear = ((logs[-1]-t).exp()-1 for t in (hp,linear))
            potentials.append(point(r,trend.exp(),part))
            gaps.append(point(r,(gap_hp if cfg['method']=='hp' else gap_linear)*100,part))
            sensitivity.append(point(r,(gap_hp-gap_linear)*100,part))
    reason = 'short' if compatible else 'metadata'
    result.extend([metric('gap','percent',gaps,reason),metric('potential','RUB',potentials,reason),metric('sensitivity','pp',sensitivity,reason)])
    return result


def calendar_changes(rows, horizons, returns=False):
    dates = [date.fromisoformat(r['period_end']) for r in rows]; result = []
    for months in horizons:
        points = []
        for i,r in enumerate(rows):
            anchor = minus_months(dates[i],months); at = bisect_right(dates,anchor)-1
            if at<0 or (anchor-dates[at]).days>7: continue
            previous = rows[at]
            number = (value(r)/value(previous)-1)*100 if returns else value(r)-value(previous)
            points.append(point(r,number,[previous,r]))
        result.append(metric(('return_' if returns else 'spread_')+str(months)+'m','percent' if returns else 'pp',points))
    return result


def paired_metrics(rows, other, cfg, cutoff):
    first = rows[0]
    if first['indicator']=='ofz_zero_10y':
        if not other: return [metric('spread','pp',[],'missing_pair')]
        second = other[0]
        if second['indicator'] not in ('ofz_zero_1y','ofz_zero_2y') or any(first[k]!=second[k] for k in ('country','source_id','methodology','frequency','unit','adjustment')):
            return [metric('spread','pp',[],'metadata')]
        lookup = {r['period_end']:r for r in other}; spread_rows = []; points = []
        for r in rows:
            b = lookup.get(r['period_end'])
            if b:
                number = value(r)-value(b)
                shape = 'normal' if number>D('0.10') else 'inverted' if number<D('-0.10') else 'flat'
                points.append(point(r,number,[r,b],shape))
                spread_rows.append(dict(r,value=number,scale=D(1),paired_revision=b['revision_id']))
        result = [metric('spread','pp',points,'no_overlap')]
        if spread_rows:
            changes = calendar_changes(spread_rows,(3,6,12))
            lookup = {r['revision_id']:r['paired_revision'] for r in spread_rows}
            for m in changes:
                for p in m['points']: p['revisions'] = sorted(set(p['revisions']+[lookup[rid] for rid in p['revisions']]))
            result.extend(changes)
        return result
    if first['indicator'] not in ('key_rate','ofz_zero_1y'): return []
    if not other: return [metric('real_rate','percent',[],'missing_pair')]
    second = other[0]
    if second['indicator']!='inflation_expectations_12m' or second['frequency']!='monthly' or second['unit']!='percent' or second['country']!=first['country'] or second['methodology'] in ('unknown','legacy_unknown') or second['adjustment']!='nsa':
        return [metric('real_rate','percent',[],'expectations')]
    if first['indicator']=='key_rate' and not cfg['hold_rate']: return [metric('real_rate','percent',[],'assumption')]
    nominal, expected = rows[-1], other[-1]
    if (date.fromisoformat(cutoff[:10])-date.fromisoformat(nominal['period_end'])).days>7 or (date.fromisoformat(cutoff[:10])-date.fromisoformat(expected['period_end'])).days>45:
        return [metric('real_rate','percent',[],'stale')]
    number = ((1+value(nominal)/100)/(1+value(expected)/100)-1)*100
    estimate = point(nominal,number,[nominal,expected], 'hold_key_rate_12m' if first['indicator']=='key_rate' else 'yield_1y_expected_12m')
    estimate['date'] = cutoff[:10]  # A current conditional estimate, not a backdated realized rate.
    return [metric('real_rate','percent',[estimate])]


def validate_rows(rows):
    first=rows[0]; seen=set()
    for r in rows:
        if any(r[k]!=first[k] for k in ('series_id','indicator','country','unit','frequency','scale','adjustment','base_year','methodology','source_id')): raise RecordError(ERROR)
        key=(r['period_start'],r['period_end'])
        if key in seen: raise RecordError(ERROR)
        seen.add(key)
        series=Series(r['indicator'],r['country'],'selected',r['unit'],r['frequency'],D(r['scale']),r['adjustment'],r['base_year'],r['methodology'])
        series.validate(); Point(r['period_start'],r['period_end'],D(r['value'])).validate(series)


def calculate(rows, other, cfg, cutoff, mode):
    """Rows are selected by the as-of reader; no provider or persistence dependency."""
    with localcontext() as ctx:
        ctx.prec=50
        ctx.rounding=ROUND_HALF_EVEN
        return _calculate(rows,other,cfg,cutoff,mode)


def _calculate(rows, other, cfg, cutoff, mode):
    warnings = ['latest'] if mode=='latest' else []
    rows=sorted(rows,key=lambda r:r['period_end']); other=sorted(other,key=lambda r:r['period_end'])
    if len(rows)+len(other)>10000: raise RecordError('Для одного расчёта допускается не более 10000 наблюдений')
    if not rows: return dict(metrics=[],warnings=warnings,status='INSUFFICIENT_DATA',reason='short')
    for group in (rows,other):
        if group: validate_rows(group)
    first=rows[0]; code=first['indicator']; freq=first['frequency']; unit=first['unit']; reason=''
    if any(r['publication_precision']=='unknown' for r in rows+other): warnings.append('unknown_publication')
    if any(r['status']=='revised' for r in rows+other): warnings.append('revised')
    if freq in ('monthly','quarterly','annual') and any(period_number(b)-period_number(a)!=1 for a,b in zip(rows,rows[1:])): warnings.append('gaps')
    max_age={'daily':7,'monthly':75,'quarterly':200,'annual':550}.get(freq,0)
    if (date.fromisoformat(cutoff[:10])-date.fromisoformat(rows[-1]['period_end'])).days>max_age: warnings.append('stale')
    if first['methodology'] in ('unknown','legacy_unknown') or first['adjustment']=='unknown': reason='metadata'
    elif code in INDEX_GROWTH | PERCENT_GROWTH:
        if unit!=('index' if code in INDEX_GROWTH else 'percent') or freq not in (('quarterly','annual') if code.startswith('gdp_') else ('monthly',)) or (code=='gdp_real_qoq_sa_growth' and (freq!='quarterly' or first['adjustment']!='sa')): reason='metadata'
        elif any(value(r)<=0 if code in INDEX_GROWTH else value(r)<=-100 for r in rows): reason='nonpositive'
        else: metrics=growth_metrics(rows)
    elif code in ('gdp_real_level','gdp_real_level_sa'):
        if unit!='RUB' or freq not in ('quarterly','annual') or first['base_year'] is None: reason='metadata'
        elif any(value(r)<=0 for r in rows): reason='nonpositive'
        else: metrics=gdp_metrics(rows,cfg); warnings.append('model')
    elif code in MARKETS:
        if unit!='index' or freq!='daily': reason='metadata'
        elif any(value(r)<=0 for r in rows): reason='nonpositive'
        else: metrics=calendar_changes(rows,(1,3,6,12),True)
    elif code in RATES:
        if unit!='percent' or freq!='daily': reason='metadata'
        elif any(value(r)<=-100 for r in rows+other): reason='nonpositive'
        else:
            changes=[point(b,(value(b)-value(a))*100,[a,b]) for a,b in zip(rows,rows[1:]) if (date.fromisoformat(b['period_end'])-date.fromisoformat(a['period_end'])).days<=7]
            metrics=[metric('change_bp','bp',changes)]+paired_metrics(rows,other,cfg,cutoff)
    else: reason='unsupported'
    if reason: return dict(metrics=[],warnings=warnings,status='INSUFFICIENT_DATA',reason=reason)
    status='OK' if all(m['points'] and m['points'][-1]['date']>=rows[-1]['period_end'] for m in metrics) else 'PARTIAL' if any(m['points'] for m in metrics) else 'INSUFFICIENT_DATA'
    return dict(metrics=metrics,warnings=warnings,status=status,reason='')
