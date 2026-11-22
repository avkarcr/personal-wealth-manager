"""Transparent five-phase similarity model. Pure Decimal arithmetic, no I/O."""
from decimal import Decimal as D, localcontext, ROUND_HALF_EVEN
from .config import FEATURES, GROUPS

PHASES = dict(RECOVERY='Восстановление', EARLY_EXPANSION='Ранняя стадия роста', LATE_EXPANSION='Поздняя стадия роста', SLOWDOWN='Замедление', RECESSION='Рецессия', INSUFFICIENT_DATA='Недостаточно данных')
DESCRIPTIONS = {
    'RECOVERY':'Активность ещё слаба, но динамика улучшается; модель проверяет поддержку других групп.',
    'EARLY_EXPANSION':'Рост положительный, импульс не ухудшается; инфляция и политика ближе к нейтральным ориентирам модели.',
    'LATE_EXPANSION':'Рост сопровождается инфляционным давлением или ужесточением политики.',
    'SLOWDOWN':'Импульс роста ослабевает; годовой рост при этом может оставаться положительным.',
    'RECESSION':'Сокращение ВВП подтверждается ещё одним показателем слабой активности.',
    'INSUFFICIENT_DATA':'Модель не может надёжно выбрать фазу при текущем составе данных и настройках.',
}
CRITERIA = {
    'RECOVERY':'QoQ SA > 0,1% или ускорение YoY > 0,1 п.п.; при этом YoY < 1% или Output Gap < 0.',
    'EARLY_EXPANSION':'YoY > 0 или QoQ SA > 0; при этом QoQ SA > 0 или ускорение YoY > −0,1 п.п.',
    'LATE_EXPANSION':'YoY > 0 или QoQ SA > 0; при этом CPI YoY > 5%, SAAR > 5% или изменение ключевой ставки за 3 месяца > 0,25 п.п.',
    'SLOWDOWN':'Ускорение YoY < −0,1 п.п. или QoQ SA < 0.',
    'RECESSION':'YoY < 0 или QoQ SA < 0; отрицательны минимум два из четырёх: YoY, QoQ SA, Output Gap, рост промышленности.',
}

# Ordered as FEATURES. All targets/thresholds are versioned hypotheses, not official dating.
TARGETS = {
 'RECOVERY': '-.2 .5 -.5 .7 .3 .3 | -.25 -.5 -.3 -.2 -.4 | -.6 0 .2 | .4 .4 .7 | .6 .6 -.6',
 'EARLY_EXPANSION': '.5 .6 -.1 .3 .6 .5 | 0 0 0 0 0 | -.2 0 0 | .5 .5 .1 | .6 .3 -.2',
 'LATE_EXPANSION': '.6 .3 .6 -.1 .4 .3 | .5 .5 .6 .4 .4 | .6 .5 .5 | .1 .1 -.5 | .2 -.3 .6',
 'SLOWDOWN': '.1 -.2 .2 -.7 -.1 -.3 | .3 -.1 .2 .2 0 | .2 .6 .5 | -.3 -.3 -.2 | -.4 .1 .1',
 'RECESSION': '-.6 -.6 -.6 -.3 -.6 -.6 | -.1 -.6 -.4 -.2 -.5 | -.5 .2 .3 | 0 0 .3 | -.5 .4 -.5',
}
TARGETS = {p:dict(zip(FEATURES,map(D,v.replace('|','').split()))) for p,v in TARGETS.items()}
REASONS = dict(coverage='Недостаточно покрытия активных весов: требуется не менее 60%.', groups='Нужны минимум три группы с покрытием каждой не менее 25%.',
               activity='Нужны годовой рост ВВП и его ускорение или квартальный рост SA.',
               ambiguous='Две лучшие фазы слишком близки: разница менее 3 баллов.', criteria='Количественные условия лучшей фазы не выполнены.')
TRANSITIONS = dict(confirmed='Фаза подтверждена двумя срезами и новым периодом ВВП.', provisional='Предварительная оценка: нет подтверждения новым периодом ВВП.',
                   transition='Возможная смена фазы: требуется подтверждение следующим срезом.', held='Предыдущая фаза сохранена: преимущество новой менее 5 баллов.', insufficient='Фаза не определена; предыдущая оценка не переносится.')


def clip(v): return max(D(-1),min(D(1),v))


def criteria(values):
    def above(k,v=0): return values[k] is not None and values[k]>D(str(v))
    def below(k,v=0): return values[k] is not None and values[k]<D(str(v))
    growth=above('gdp_yoy') or above('gdp_qoq')
    contraction=below('gdp_yoy') or below('gdp_qoq')
    return dict(RECOVERY=(above('gdp_qoq',.1) or above('gdp_accel',.1)) and (below('gdp_yoy',1) or below('output_gap')),
                EARLY_EXPANSION=growth and (above('gdp_qoq') or above('gdp_accel',-.1)),
                LATE_EXPANSION=growth and (above('cpi_yoy',5) or above('cpi_saar',5) or above('key_change',.25)),
                SLOWDOWN=below('gdp_accel',-.1) or below('gdp_qoq'),
                RECESSION=contraction and sum(below(k) for k in ('gdp_yoy','gdp_qoq','output_gap','industry'))>=2)


def score(evidence,cfg):
    """Conditional fit 0..100; coverage remains visible and separately gates output."""
    with localcontext() as ctx:
        ctx.prec=50; ctx.rounding=ROUND_HALF_EVEN
        return _score(evidence,cfg)


def _score(evidence,cfg):
    scores={p:D(0) for p in TARGETS}; covered={g:D(0) for g in GROUPS}; details={}; contributions={p:{g:D(0) for g in GROUPS} for p in TARGETS}
    for k,(group,*_) in FEATURES.items():
        f=cfg['features'][k]; weight=D(cfg['groups'][group])*D(f['weight'])/100 if f['enabled'] else D(0)
        value=evidence[k]['value']; norm=clip((value-D(f['center']))/D(f['scale'])) if value is not None and weight else None
        fits={p:100*(1-abs(norm-t[k])/2) for p,t in TARGETS.items()} if norm is not None else {}
        if norm is not None:
            covered[group]+=weight
            for p,fit in fits.items(): contributions[p][group]+=fit*weight; scores[p]+=fit*weight
        details[k]=dict(evidence[k],weight=weight,normalized=norm,fits=fits)
    coverage=sum(covered.values())
    if coverage:
        scores={p:v/coverage for p,v in scores.items()}
        contributions={p:{g:v/coverage for g,v in groups.items()} for p,groups in contributions.items()}
    ranked=sorted(scores,key=lambda p:scores[p],reverse=True); winner=ranked[0]; margin=scores[winner]-scores[ranked[1]]
    values={k:d['value'] if d['normalized'] is not None else None for k,d in details.items()}; eligible=criteria(values)
    reason=''
    if coverage<60: reason='coverage'
    elif sum(D(cfg['groups'][g])>0 and covered[g]>=D(cfg['groups'][g])/4 for g in GROUPS)<3: reason='groups'
    elif values['gdp_yoy'] is None or (values['gdp_qoq'] is None and values['gdp_accel'] is None): reason='activity'
    elif margin<3: reason='ambiguous'
    elif not eligible[winner]: reason='criteria'
    phase='INSUFFICIENT_DATA' if reason else winner
    return dict(phase=phase,candidate=winner if coverage else None,reason=reason,scores=scores,margin=margin,coverage=coverage,
                covered=covered,contributions=contributions,features=details,eligible=eligible)


def assess(evidence,previous,cfg):
    with localcontext() as ctx:
        ctx.prec=50; ctx.rounding=ROUND_HALF_EVEN
        return _assess(evidence,previous,cfg)


def _assess(evidence,previous,cfg):
    now=score(evidence,cfg); then=score(previous,cfg); phase=now['phase']; prior=then['phase']
    transition='insufficient'; factor=D(0)
    if phase!='INSUFFICIENT_DATA':
        transition='provisional'; factor=D('.6')
        new_period=(evidence['gdp_yoy']['date'] or '')>(previous['gdp_yoy']['date'] or '')
        if prior==phase and new_period: transition='confirmed'; factor=D(1)
        elif prior!='INSUFFICIENT_DATA' and prior!=phase:
            if now['eligible'][prior] and now['scores'][phase]-now['scores'][prior]<5:
                phase=prior; transition='held'; factor=D('.5')
            else: transition='transition'
    supports=[]; contradictions=[]
    if phase!='INSUFFICIENT_DATA':
        for k,detail in now['features'].items():
            if not detail['fits']: continue
            advantage=detail['fits'][phase]-max(v for p,v in detail['fits'].items() if p!=phase)
            if advantage>=5: supports.append(k)
            elif advantage<=-10: contradictions.append(k)
    # Held phases have low conviction: use absolute separation, with the extra stability penalty.
    confidence=(now['coverage']*now['scores'][phase]/100*min(D(1),now['margin']/10)*factor) if phase!='INSUFFICIENT_DATA' else D(0)
    now.update(phase=phase,confidence=confidence,transition=transition,supports=supports,contradictions=contradictions,
               previous=dict(phase=prior,candidate=then['candidate'],coverage=then['coverage'],reason=then['reason']),
               group_contributions=now['contributions'].get(phase,{g:D(0) for g in GROUPS}))
    return now
