"""Versioned, explicit series bindings and bounded expert hypotheses (not fitted)."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext, ROUND_DOWN
from wealth_manager.shared.records import RecordError
from wealth_manager.modules.macroeconomics.derived import options

D = Decimal
ERROR = 'Некорректные настройки модели цикла'
VERSION = 'economic-cycle-v1'
GROUPS = dict(activity='Экономическая активность', inflation='Инфляция', policy='Денежно-кредитная политика', curve='Кривая доходности', markets='Рынки')
# group, label, within-group weight, center, scale. Units: percent or percentage points.
FEATURES = {
    'gdp_yoy': ('activity', 'ВВП: рост год к году', 25, 0, 4),
    'gdp_qoq': ('activity', 'ВВП: рост квартал к кварталу SA', 25, 0, 1),
    'output_gap': ('activity', 'Output Gap — оценка модели', 20, 0, 3),
    'gdp_accel': ('activity', 'Ускорение годового роста ВВП', 20, 0, 1),
    'industry': ('activity', 'Промышленность: рост год к году', 5, 0, 5),
    'employment': ('activity', 'Снижение безработицы за 3 месяца', 5, 0, 1),
    'cpi_yoy': ('inflation', 'Инфляция год к году', 30, 4, 4),
    'cpi_accel': ('inflation', 'Ускорение годовой инфляции', 20, 0, 1),
    'cpi_saar': ('inflation', 'Инфляция 3M SAAR', 20, 4, 4),
    'core_yoy': ('inflation', 'Базовая инфляция год к году', 15, 4, 4),
    'expectations': ('inflation', 'Изменение ожиданий за 3 месяца', 15, 0, 2),
    'key_change': ('policy', 'Изменение ключевой ставки за 3 месяца', 40, 0, 2),
    'real_rate': ('policy', 'Реальная ставка ex ante', 40, 2, 4),
    'key_level': ('policy', 'Уровень ключевой ставки', 20, 8, 8),
    'spread_1': ('curve', 'ОФЗ: спред 10 лет — 1 год', 35, 0, '1.5'),
    'spread_2': ('curve', 'ОФЗ: спред 10 лет — 2 года', 35, 0, '1.5'),
    'curve_change': ('curve', 'Изменение спреда 10 лет — 2 года за 3 месяца', 30, 0, '0.5'),
    'equities': ('markets', 'Акции: доходность за 3 месяца', 45, 0, 10),
    'bonds': ('markets', 'Облигации: доходность за 3 месяца', 35, 0, 5),
    'money': ('markets', 'Изменение RUSFAR за 3 месяца', 20, 0, 2),
}
# Role -> indicator codes, frequency. No automatic provider or index substitution.
ROLES = {
    'gdp': ({'gdp_real_level', 'gdp_real_level_sa', 'gdp_real_yoy_growth', 'gdp_volume_yoy_index'}, 'quarterly'),
    'gdp_qoq': ({'gdp_real_qoq_sa_growth'}, 'quarterly'),
    'cpi': ({'cpi_yoy_index', 'cpi_yoy_growth', 'cpi_mom_index', 'cpi_mom_growth'}, 'monthly'),
    'cpi_sa': ({'cpi_mom_sa_index', 'cpi_mom_sa_growth'}, 'monthly'),
    'core': ({'core_cpi_mom_index'}, 'monthly'),
    'expectations': ({'inflation_expectations_12m'}, 'monthly'),
    'industry': ({'industrial_yoy_index'}, 'monthly'),
    'unemployment': ({'unemployment_rate'}, 'monthly'),
    'key_rate': ({'key_rate'}, 'daily'),
    'yield_1y': ({'ofz_zero_1y'}, 'daily'),
    'yield_2y': ({'ofz_zero_2y'}, 'daily'),
    'yield_10y': ({'ofz_zero_10y'}, 'daily'),
    'equities': ({'moex_imoex', 'moex_mcftr'}, 'daily'),
    'bonds': ({'moex_rgbi', 'moex_rgbitr'}, 'daily'),
    'money': ({'moex_rusfar'}, 'daily'),
}
ROLE_LABELS = dict(gdp='ВВП: уровень или годовой рост', gdp_qoq='ВВП: отдельный ряд квартального роста SA', cpi='ИПЦ', cpi_sa='ИПЦ с сезонной корректировкой', core='Базовый ИПЦ', expectations='Ожидания инфляции на 12 месяцев', industry='Промышленное производство', unemployment='Безработица', key_rate='Ключевая ставка', yield_1y='ОФЗ 1 год', yield_2y='ОФЗ 2 года', yield_10y='ОФЗ 10 лет', equities='Индекс акций', bonds='Индекс облигаций', money='RUSFAR')


def defaults():
    return dict(groups=dict(zip(GROUPS, ('40','20','15','15','10'))),
                features={k:dict(enabled=True, weight=str(v[2]), center=str(v[3]), scale=str(v[4])) for k,v in FEATURES.items()},
                series={k:None for k in ROLES}, method='hp', window=40, hold_rate=False, real_rate_basis='yield_1y')


def number(value, low, high):
    try:
        if isinstance(value, bool): raise ValueError()
        result = D(str(value))
        if not result.is_finite() or not low <= result <= high: raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError): raise RecordError(ERROR)


def shares(weights):
    """Finite exact sum of 100; residual to the last positive item, deterministic."""
    positive = {k:v for k,v in weights.items() if v>0}
    if not positive: return {k:D(0) for k in weights}
    with localcontext() as ctx:
        ctx.prec = 50
        total = sum(positive.values())
        result = {k:(v*100/total).quantize(D('0.000000000001'), rounding=ROUND_DOWN) for k,v in weights.items()}
        result[next(reversed(positive))] += 100-sum(result.values())
        return result


def compatible(role, row):
    codes, frequency = ROLES[role]
    code = row['indicator']
    unit = 'RUB' if code in ('gdp_real_level','gdp_real_level_sa') else 'index' if code.endswith('_index') or role in ('equities','bonds') else 'percent'
    adjustment = 'sa' if role in ('cpi_sa','gdp_qoq') or code=='gdp_real_level_sa' else None
    return (code in codes and row['country']=='RU' and row['frequency']==frequency and row['unit']==unit
            and (adjustment is None or row['adjustment']==adjustment))


def validate(config, catalog=None):
    cfg = deepcopy(config)
    if not isinstance(cfg, dict) or set(cfg)!=set(defaults()): raise RecordError(ERROR)
    if any(not isinstance(cfg[k],dict) for k in ('groups','features','series')): raise RecordError(ERROR)
    if set(cfg['groups'])!=set(GROUPS) or set(cfg['features'])!=set(FEATURES) or set(cfg['series'])!=set(ROLES): raise RecordError(ERROR)
    weights = {k:number(cfg['groups'][k],0,10000) for k in GROUPS}
    if not sum(weights.values()): raise RecordError(ERROR)
    cfg['groups'] = {k:str(v) for k,v in shares(weights).items()}
    for group in GROUPS:
        active = {}
        for code,definition in FEATURES.items():
            if definition[0]!=group: continue
            f = cfg['features'][code]
            if not isinstance(f,dict) or set(f)!={'enabled','weight','center','scale'} or type(f['enabled'])!=bool: raise RecordError(ERROR)
            weight = number(f['weight'],0,10000)
            f.update(center=str(number(f['center'],-10000,10000)), scale=str(number(f['scale'],D('0.000001'),10000)))
            active[code] = weight if f['enabled'] else D(0)
        if weights[group]>0 and not sum(active.values()): raise RecordError(ERROR)
        for code,weight in shares(active).items(): cfg['features'][code]['weight']=str(weight)
    for role,sid in cfg['series'].items():
        if sid is not None and (type(sid)!=int or sid<=0 or (catalog is not None and (sid not in catalog or not compatible(role,catalog[sid])))): raise RecordError(ERROR)
    options(cfg['method'],cfg['window'],cfg['hold_rate'])
    if cfg['real_rate_basis'] not in ('yield_1y','key_rate'): raise RecordError(ERROR)
    return cfg
