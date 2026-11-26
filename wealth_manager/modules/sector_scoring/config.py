"""Versioned expert assumptions. None of these thresholds are fitted forecasts."""
from copy import deepcopy
from decimal import Decimal as D, InvalidOperation
from wealth_manager.modules.economic_cycle.config import shares
from wealth_manager.shared.decimal import exact_text
from wealth_manager.shared.records import RecordError

VERSION = 'sector-scoring-v1'
ERROR = 'Некорректные настройки отраслевой модели'
GROUPS = dict(macro='Макроэкономическая совместимость', fundamentals='Фундаментальные характеристики',
              valuation='Рыночная оценка', markets='Рыночные характеристики')
PHASE_CODES = ('RECOVERY', 'EARLY_EXPANSION', 'LATE_EXPANSION', 'SLOWDOWN', 'RECESSION')
AUTO = {'phase', 'rate', 'inflation', 'growth'}
PROFILES = dict(general='Нефинансовые компании', financial='Финансовые организации',
                utilities='Электроэнергетика', resources='Сырьевые отрасли', gold='Золотодобыча')
# group, label, unit, score-0 anchor, score-100 anchor, maximum age in days.
# For automatic macro factors, anchors map to -1..1 before sector sensitivity.
METRICS = {
    'phase': ('macro', 'Совместимость с фазой', 'score', 0, 100, 0),
    'rate': ('macro', 'Изменение ставки за 3 месяца', 'pp', -2, 2, 0),
    'inflation': ('macro', 'Ускорение инфляции', 'pp', -2, 2, 0),
    'growth': ('macro', 'Рост реального ВВП', 'percent', -5, 5, 0),
    'currency': ('macro', 'Сценарный эффект валютного курса', 'score', -100, 100, 90),
    'commodity': ('macro', 'Сценарный эффект цен на сырьё', 'score', -100, 100, 90),
    'revenue_growth': ('fundamentals', 'Рост выручки год к году', 'percent', -10, 20, 550),
    'roe': ('fundamentals', 'ROE', 'percent', 0, 25, 550),
    'roic': ('fundamentals', 'ROIC', 'percent', 0, 20, 550),
    'net_debt_ebitda': ('fundamentals', 'Net Debt / EBITDA', 'ratio', 5, 0, 550),
    'fcf_margin': ('fundamentals', 'FCF / выручка', 'percent', -10, 20, 550),
    'capex_sales': ('fundamentals', 'CAPEX / выручка', 'percent', 30, 5, 550),
    'dividend_cover': ('fundamentals', 'Покрытие дивидендов денежным потоком', 'ratio', 0, 2, 550),
    'capital_buffer': ('fundamentals', 'Запас капитала над нормативом', 'pp', 0, 5, 550),
    'pe': ('valuation', 'P/E', 'ratio', 25, 5, 45),
    'ev_ebitda': ('valuation', 'EV/EBITDA', 'ratio', 15, 3, 45),
    'pbv': ('valuation', 'P/BV', 'ratio', 3, '0.5', 45),
    'dividend_yield': ('valuation', 'Дивидендная доходность', 'percent', 0, 15, 45),
    'valuation_discount': ('valuation', 'Дисконт к сопоставимой оценке', 'percent', -30, 30, 45),
    'relative_return': ('markets', 'Доходность к ориентиру за 3 месяца', 'pp', -15, 15, 45),
    'liquidity': ('markets', 'Средний дневной оборот за 20 сессий', 'million_rub', 0, 100, 45),
    'volatility': ('markets', 'Годовая волатильность', 'percent', 60, 15, 45),
    'trend': ('markets', 'Отклонение от средней за 200 сессий', 'percent', -20, 20, 45),
    'sector_return': ('markets', 'Доходность отрасли за 3 месяца', 'percent', -20, 20, 45),
}
UNITS = dict(score='Баллы', pp='п.п.', percent='%', ratio='Коэффициент', million_rub='млн ₽')
EXCLUSIONS = {
    'financial': {'revenue_growth', 'roic', 'net_debt_ebitda', 'fcf_margin', 'capex_sales', 'dividend_cover', 'ev_ebitda'},
    'general': {'roe', 'capital_buffer', 'pbv'},
    'utilities': {'roe', 'capital_buffer', 'pbv'},
    'resources': {'roe', 'capital_buffer', 'pbv'},
    'gold': {'roe', 'capital_buffer', 'pbv'},
}


def number(value, low=-1000000, high=1000000):
    try:
        if isinstance(value, bool) or len(str(value)) > 80: raise ValueError()
        result = D(str(value))
        if not result.is_finite() or not D(str(low)) <= result <= D(str(high)): raise ValueError()
        if result and result.as_tuple().exponent < -12: raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise RecordError(ERROR)


def defaults():
    return dict(groups=dict(macro='25', fundamentals='30', valuation='25', markets='20'),
                features={k:dict(enabled=True, weight='1', bad=str(v[3]), good=str(v[4]), max_age=v[5]) for k,v in METRICS.items()},
                min_coverage='60', min_sample='50', min_liquidity='1', sectors={})


def profile_rule(profile='general'):
    phases = {
        'general': (70,90,55,25,10), 'financial': (75,85,60,25,10),
        'utilities': (55,55,60,70,65), 'resources': (65,85,70,30,15), 'gold': (55,40,60,75,80),
    }
    sensitivity = {'general': (-1,'-.5',1), 'financial': (0,'-.4','.8'),
                   'utilities': ('-.8',0,'.2'), 'resources': ('-.4','.2','.7'), 'gold': ('-.4','.4','-.1')}
    bounds = {'utilities': {'net_debt_ebitda': ('6','1'), 'capex_sales': ('50','10'), 'roic': ('3','12')},
              'resources': {'pe': ('18','4')}, 'gold': {'pe': ('25','6'), 'capex_sales': ('50','5')}}
    return dict(enabled=True, profile=profile, phases=dict(zip(PHASE_CODES,map(str,phases[profile]))),
                sensitivity=dict(zip(('rate','inflation','growth'),map(str,sensitivity[profile]))),
                not_applicable=[], bounds={k:dict(bad=a,good=b) for k,(a,b) in bounds.get(profile,{}).items()})


def rule_for(sector, sectors, config):
    own = config['sectors'].get(str(sector['id']))
    if own is not None: return deepcopy(own)
    parent = next((s for s in sectors if s['id'] == sector['parent_id']), None)
    if parent: return rule_for(parent, sectors, config)
    profile = {'finance':'financial', 'electricity':'utilities', 'oil_gas':'resources',
               'metals_mining':'resources', 'chemicals':'resources'}.get(sector['code'],'general')
    return profile_rule(profile)


def validate(config, sectors=None):
    cfg = deepcopy(config)
    if not isinstance(cfg,dict) or set(cfg) != set(defaults()): raise RecordError(ERROR)
    if any(not isinstance(cfg[k],dict) for k in ('groups','features','sectors')): raise RecordError(ERROR)
    if set(cfg['groups']) != set(GROUPS) or set(cfg['features']) != set(METRICS): raise RecordError(ERROR)
    weights = {k:number(v,0,10000) for k,v in cfg['groups'].items()}
    if not sum(weights.values()): raise RecordError(ERROR)
    cfg['groups'] = {k:exact_text(v) for k,v in shares(weights).items()}
    for k,f in cfg['features'].items():
        if not isinstance(f,dict) or set(f) != {'enabled','weight','bad','good','max_age'} or type(f['enabled']) != bool: raise RecordError(ERROR)
        for field in ('weight','bad','good'): f[field] = exact_text(number(f[field],0 if field=='weight' else -1000000,10000 if field=='weight' else 1000000))
        if f['bad'] == f['good'] or type(f['max_age']) != int or not 0 <= f['max_age'] <= 3650 or (k not in AUTO and not f['max_age']): raise RecordError(ERROR)
    for group in GROUPS:
        if weights[group] and not any(v['enabled'] and D(v['weight']) for k,v in cfg['features'].items() if METRICS[k][0]==group): raise RecordError(ERROR)
    for key,low,high in [('min_coverage',1,100),('min_sample',1,100),('min_liquidity',0,1000000)]: cfg[key] = exact_text(number(cfg[key],low,high))
    ids = {str(s['id']) for s in sectors} if sectors is not None else None
    for sid,rule in cfg['sectors'].items():
        if not isinstance(sid,str) or not sid.isdigit() or int(sid)<=0 or str(int(sid))!=sid or (ids is not None and sid not in ids): raise RecordError(ERROR)
        if not isinstance(rule,dict) or set(rule) != set(profile_rule()) or type(rule['enabled']) != bool or rule['profile'] not in PROFILES: raise RecordError(ERROR)
        for key,keys,low,high in [('phases',set(PHASE_CODES),0,100),('sensitivity',{'rate','inflation','growth'},-1,1)]:
            if not isinstance(rule[key],dict) or set(rule[key]) != keys: raise RecordError(ERROR)
            rule[key] = {k:exact_text(number(v,low,high)) for k,v in rule[key].items()}
        excluded = rule['not_applicable']
        if not isinstance(excluded,list) or any(not isinstance(k,str) or k not in METRICS for k in excluded) or len(excluded)!=len(set(excluded)): raise RecordError(ERROR)
        if not isinstance(rule['bounds'],dict): raise RecordError(ERROR)
        for code,bound in rule['bounds'].items():
            if code not in METRICS or not isinstance(bound,dict) or set(bound)!={'bad','good'}: raise RecordError(ERROR)
            rule['bounds'][code]={k:exact_text(number(v)) for k,v in bound.items()}
            if rule['bounds'][code]['bad']==rule['bounds'][code]['good']: raise RecordError(ERROR)
    return cfg
