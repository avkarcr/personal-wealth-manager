"""Pure scoring: separate applicability, missing evidence, coverage and eligibility."""
from decimal import Decimal as D, localcontext
from wealth_manager.shared.decimal import exact_text, rounded
from .config import GROUPS, METRICS, AUTO, EXCLUSIONS, rule_for, shares
from .domain import select_observations, usable

REASONS = dict(missing='Нет доступного наблюдения', stale='Показатель устарел', sample='Недостаточный охват эмитентов',
               unavailable='Значение отозвано или недоступно', not_meaningful='Экономически неприменимое значение',
               disabled='Фактор отключён', not_applicable='Не применяется к профилю отрасли',
               coverage='Недостаточно покрытия для итогового балла', groups='Нужны две немакроэкономические группы с покрытием не менее 50% каждой',
               liquidity='Недостаточная ликвидность', liquidity_unknown='Ликвидность не подтверждена',
               excluded='Отрасль исключена из оценки', archived='Отрасль или её родитель в архиве')
AUTO_FEATURES = dict(rate='key_change', inflation='cpi_accel', growth='gdp_yoy')


def normalized(value,bad,good):
    return max(D(0),min(D(100),(D(str(value))-D(bad))*100/(D(good)-D(bad))))


def assess(sectors, observations, cycle, config, cutoff, mode):
    with localcontext() as ctx:
        ctx.prec=50
        return _assess(sectors,observations,cycle,config,cutoff,mode)


def _assess(sectors, observations, cycle, config, cutoff, mode):
    selected=select_observations(observations,cutoff,mode)
    results=[]
    for sector in sorted(sectors,key=lambda s:s['id']):
        rule=rule_for(sector,sectors,config)
        excluded=set(rule['not_applicable']) | EXCLUSIONS[rule['profile']]
        excluded.update(k for k,v in rule['sensitivity'].items() if not D(v))
        weights={}
        for group in GROUPS:
            within={k:D(config['features'][k]['weight']) if config['features'][k]['enabled'] and k not in excluded else D(0) for k,v in METRICS.items() if v[0]==group}
            weights.update({k:D(config['groups'][group])*v/100 for k,v in shares(within).items()})
        covered={g:D(0) for g in GROUPS}; contributions={g:D(0) for g in GROUPS}; details={}
        for code,definition in METRICS.items():
            group=definition[0];f=config['features'][code];bounds=rule['bounds'].get(code,f)
            row=selected.get((sector['id'],code));reason='';score=None;quality=D(1)
            value=row['value'] if row else None;date=row['period_end'] if row else None
            if code in excluded: reason='not_applicable'
            elif not weights[code]: reason='disabled'
            elif code=='phase':
                if cycle['phase'] in rule['phases']:
                    value=cycle['phase']; score=D(rule['phases'][value]); quality=D(str(cycle['coverage']))/100; date=cutoff[:10]
                else: reason='missing'
            elif code in AUTO_FEATURES:
                feature=cycle['features'].get(AUTO_FEATURES[code],{})
                value=feature.get('value');date=feature.get('date')
                if value is None: reason='missing'
                else: score=50+(normalized(value,bounds['bad'],bounds['good'])-50)*D(rule['sensitivity'][code])
            else:
                reason=usable(row,code,f,cutoff,config)
                if not reason:
                    score=normalized(value,bounds['bad'],bounds['good']);quality=D(row['sample_size'])/row['population_size']
            effective=weights[code]*quality if score is not None else D(0)
            contribution=(score-50)*effective/100 if score is not None else D(0)
            covered[group]+=effective;contributions[group]+=contribution
            details[code]=dict(value=value,date=date,score=score,weight=weights[code],covered=effective,contribution=contribution,
                               reason=reason,observation=row,bad=bounds['bad'],good=bounds['good'],estimated=code in ('phase','currency','commodity'))
        coverage=sum(covered.values());total=50+sum(contributions.values())
        parent=next((s for s in sectors if s['id']==sector['parent_id']),None)
        reason=''
        liquid=selected.get((sector['id'],'liquidity'))
        if sector['archived'] or (parent and parent['archived']): reason='archived'
        elif not rule['enabled'] or (parent and not rule_for(parent,sectors,config)['enabled']): reason='excluded'
        elif D(config['min_liquidity'])>0 and usable(liquid,'liquidity',config['features']['liquidity'],cutoff,config): reason='liquidity_unknown'
        elif D(config['min_liquidity'])>0 and D(liquid['value'])<D(config['min_liquidity']): reason='liquidity'
        elif coverage<D(config['min_coverage']): reason='coverage'
        elif sum(D(config['groups'][g])>0 and covered[g]>=D(config['groups'][g])/2 for g in GROUPS if g!='macro')<2: reason='groups'
        signals=[k for k,v in details.items() if v['score'] is not None]
        drivers=sorted((k for k in signals if details[k]['score']>=60),key=lambda k:details[k]['contribution'],reverse=True)
        risks=sorted((k for k in signals if details[k]['score']<=40),key=lambda k:details[k]['contribution'])
        contradictions=[k for k in signals if (total>50 and details[k]['score']<=40) or (total<50 and details[k]['score']>=60)]
        results.append(dict(sector=sector,parent=parent,profile=rule['profile'],rule=rule,score=None if reason else rounded(total,2),rank=None,
                            reason=reason,coverage=coverage,covered=covered,contributions=contributions,features=details,
                            compatibility=details['phase']['score'],drivers=drivers,risks=risks,contradictions=contradictions))
    # Root sectors and each parent's children are separate comparison sets.
    cohorts={r['sector']['parent_id'] for r in results}
    for cohort in cohorts:
        ranked=sorted((r for r in results if r['sector']['parent_id']==cohort and r['score'] is not None),key=lambda r:(-r['score'],r['sector']['id']))
        last=None;rank=None
        for index,row in enumerate(ranked,1):
            if row['score']!=last: rank=index
            row['rank']=rank;last=row['score']
    return dict(sectors=results,inputs=sorted({r['id'] for r in selected.values()}))
