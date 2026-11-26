"""M10 economics: explicit score examples, applicability and incomplete evidence."""
from copy import deepcopy
from decimal import Decimal as D, localcontext
import unittest
from wealth_manager.modules.sector_scoring.config import defaults, validate, rule_for, profile_rule, METRICS, GROUPS, AUTO
from wealth_manager.modules.sector_scoring.domain import select_observations, usable, Observation
from wealth_manager.modules.sector_scoring.model import assess, normalized
from wealth_manager.shared.records import RecordError

CUTOFF='2026-11-26T12:00:00.000000+00:00'
EMPTY_CYCLE=dict(phase='INSUFFICIENT_DATA',coverage=0,features={})
SECTORS=[dict(id=1,code='technology',name='IT',name_en='IT',parent_id=None,archived=0)]


def observation(metric,value,**changes):
    result=dict(id=list(METRICS).index(metric)+1,sector_id=1,metric=metric,value=str(value) if value is not None else None,unit=METRICS[metric][2],
                period_start='2026-11-01',period_end='2026-11-20',source='Synthetic aggregate',methodology='Synthetic equal issuer aggregate',
                sample_size=10,population_size=10,published_at='2026-11-21T00:00:00.000000+00:00',received_at=CUTOFF,status='valid',source_url='',supersedes_id=None)
    result.update(changes);return result


def small_config():
    cfg=defaults();cfg['groups']=dict(macro='0',fundamentals='40',valuation='40',markets='20')
    for k,f in cfg['features'].items(): f['enabled']=k in ('revenue_growth','pe','liquidity')
    cfg['features']['revenue_growth'].update(bad='0',good='20')
    return validate(cfg)


class SectorModelTests(unittest.TestCase):
    def result(self,rows,config=None,sectors=None,cycle=None,mode='known'):
        return assess(sectors or SECTORS,rows,cycle or EMPTY_CYCLE,config or small_config(),CUTOFF,mode)['sectors'][0]

    def test_hand_calculated_scores_and_additive_explanations(self):
        rows=[observation('revenue_growth',10),observation('pe',10),observation('liquidity',100)]
        r=self.result(rows)
        # Growth 50*40%, valuation 75*40%, liquidity 100*20% = 70.
        self.assertEqual((r['score'],r['coverage'],r['rank']),(70,100,1))
        self.assertEqual(r['contributions'],dict(macro=0,fundamentals=0,valuation=10,markets=10))
        self.assertEqual(50+sum(r['contributions'].values()),r['score'])
        self.assertIn('pe',r['drivers']);self.assertFalse(r['risks'])
        rows[0]['value']='-10';rows[1]['value']='25'
        r=self.result(rows);self.assertEqual(r['score'],20)
        self.assertIn('revenue_growth',r['risks']);self.assertIn('liquidity',r['contradictions'])
        with localcontext() as ctx:
            ctx.prec=6;self.assertEqual(r,self.result(rows))

    def test_missing_zero_disabled_and_inapplicable_are_distinct(self):
        cfg=small_config();rows=[observation('revenue_growth',0),observation('pe',10),observation('liquidity',100)]
        r=self.result(rows);self.assertEqual(r['features']['revenue_growth']['score'],0)
        self.assertEqual(r['features']['roe']['reason'],'not_applicable')
        self.assertEqual(r['features']['trend']['reason'],'disabled')
        missing=self.result(rows[1:]);self.assertEqual(missing['features']['revenue_growth']['reason'],'missing')
        self.assertEqual(missing['coverage'],60);self.assertLess(missing['score'],100)
        cfg['features']['revenue_growth']['enabled']=False;cfg['features']['fcf_margin']['enabled']=True
        self.assertEqual(self.result(rows,cfg)['features']['revenue_growth']['reason'],'disabled')

    def test_partial_population_reduces_coverage_and_score_strength(self):
        cfg=small_config();cfg['min_coverage']='40'
        rows=[observation('revenue_growth',10,sample_size=5),observation('pe',10,sample_size=5),observation('liquidity',100,sample_size=5)]
        r=self.result(rows,cfg);self.assertEqual((r['coverage'],r['score']),(50,60))
        rows[0]['sample_size']=4;r=self.result(rows,cfg)
        self.assertEqual(r['features']['revenue_growth']['reason'],'sample');self.assertIsNone(r['score'])

    def test_macro_only_and_single_observed_group_cannot_publish_a_score(self):
        cfg=defaults();cfg.update(min_liquidity='0',min_coverage='1');cfg['groups']={g:'100' if g=='macro' else '0' for g in GROUPS}
        cycle=dict(phase='EARLY_EXPANSION',coverage=100,features={k:dict(value=D(2),date='2026-11-20') for k in ('key_change','cpi_accel','gdp_yoy')})
        r=self.result([],validate(cfg),cycle=cycle)
        self.assertEqual(r['compatibility'],90);self.assertEqual(r['reason'],'groups');self.assertIsNone(r['rank'])
        cfg['groups']={g:'100' if g=='markets' else '0' for g in GROUPS}
        r=self.result([observation('liquidity',100)],validate(cfg));self.assertEqual(r['reason'],'groups')

    def test_liquidity_guard_survives_disabling_its_weight(self):
        rows=[observation('revenue_growth',10),observation('pe',10),observation('liquidity',0)]
        r=self.result(rows);self.assertEqual(r['reason'],'liquidity');self.assertIsNone(r['score'])
        cfg=small_config();cfg['groups']['markets']='0';cfg['features']['liquidity']['enabled']=False
        r=self.result(rows,validate(cfg));self.assertEqual(r['reason'],'liquidity')
        cfg['min_liquidity']='0';self.assertIsNotNone(self.result(rows,validate(cfg))['score'])
        self.assertEqual(self.result(rows[:2])['reason'],'liquidity_unknown')

    def test_negative_multiples_are_not_cheap_and_old_or_withdrawn_values_do_not_return(self):
        for metric in ('pe','pbv','ev_ebitda'):
            self.assertEqual(usable(observation(metric,-2),metric,defaults()['features'][metric],CUTOFF,defaults()),'not_meaningful')
        old=observation('pe',5,period_end='2026-08-01')
        latest=observation('pe',None,id=90,status='unavailable',period_end='2026-10-01')
        self.assertEqual(select_observations([old,latest],CUTOFF,'known')[(1,'pe')]['id'],90)
        self.assertEqual(usable(latest,'pe',defaults()['features']['pe'],CUTOFF,defaults()),'unavailable')
        self.assertEqual(usable(old,'pe',defaults()['features']['pe'],CUTOFF,defaults()),'stale')

    def test_publication_vintages_unknown_dates_and_future_periods(self):
        original=observation('pe',10,published_at='2026-11-21T00:00:00.000000+00:00')
        revision=observation('pe',40,id=80,published_at='2026-11-27T00:00:00.000000+00:00')
        future=observation('pe',1,id=99,period_end='2026-12-01')
        self.assertEqual(select_observations([original,revision,future],CUTOFF,'known')[(1,'pe')]['value'],'10')
        self.assertEqual(select_observations([original,revision,future],CUTOFF,'latest')[(1,'pe')]['value'],'40')
        revision.update(published_at='',received_at='2026-11-27T00:00:00.000000+00:00')
        self.assertEqual(select_observations([revision],CUTOFF,'known'),{})
        with self.assertRaises(RecordError): select_observations([],CUTOFF,'bad')

    def test_date_only_publication_does_not_resurrect_a_withdrawn_revision(self):
        original=observation('pe',10,published_at='2026-11-26T23:59:59.999999+00:00')
        withdrawal=observation('pe',None,id=90,published_at='',status='unavailable')
        for mode in ('known','latest'):
            chosen=select_observations([original,withdrawal],'2026-11-27T00:00:00.000000+00:00',mode)
            self.assertEqual(chosen[(1,'pe')]['id'],90)

    def test_financial_and_resource_profiles_and_custom_bounds(self):
        cfg=validate(defaults());bank=dict(SECTORS[0],code='finance')
        rows=[observation(k,10) for k in METRICS if k not in AUTO]
        r=self.result(rows,cfg,sectors=[bank])
        self.assertEqual(r['profile'],'financial');self.assertIsNotNone(r['features']['roe']['score'])
        for k in ('ev_ebitda','net_debt_ebitda','capex_sales','rate'):
            self.assertEqual(r['features'][k]['reason'],'not_applicable')
        utility=dict(bank,code='electricity');gold=profile_rule('gold');cfg['sectors']['1']=gold
        r=self.result(rows,cfg,sectors=[utility]);self.assertEqual(r['profile'],'gold');self.assertEqual(r['features']['pe']['good'],'6')
        gold['bounds']['pe']=dict(bad='20',good='0')
        self.assertEqual(self.result(rows,cfg)['features']['pe']['score'],50)
        self.assertEqual(rule_for(utility,[utility],defaults())['profile'],'utilities')

    def test_rank_cohorts_ties_hierarchy_exclusion_and_archives(self):
        sectors=SECTORS+[dict(SECTORS[0],id=2),dict(SECTORS[0],id=3,parent_id=1),dict(SECTORS[0],id=4,parent_id=1)]
        rows=[observation(k,v,sector_id=s['id'],id=s['id']*100+i) for s in sectors for i,(k,v) in enumerate([('revenue_growth',10),('pe',10),('liquidity',100)])]
        r=assess(sectors,rows,EMPTY_CYCLE,small_config(),CUTOFF,'known')['sectors']
        self.assertEqual([x['rank'] for x in r],[1,1,1,1])
        cfg=small_config();cfg['sectors']['1']=dict(profile_rule(),enabled=False)
        r=assess(sectors,rows,EMPTY_CYCLE,cfg,CUTOFF,'known')['sectors']
        self.assertEqual([x['reason'] for x in r],['excluded','','excluded','excluded'])
        sectors[0]['archived']=1
        self.assertEqual(assess(sectors,rows,EMPTY_CYCLE,cfg,CUTOFF,'known')['sectors'][2]['reason'],'archived')

    def test_clipping_direction_and_exact_normalization(self):
        self.assertEqual(normalized(-100,'0','20'),0);self.assertEqual(normalized(50,'0','20'),100)
        self.assertEqual(normalized(10,'20','0'),50)
        cfg=defaults();cfg['groups']={g:'1' for g in GROUPS}
        self.assertEqual(sum(map(D,validate(cfg)['groups'].values())),100)

    def test_invalid_configs_do_not_reach_a_model(self):
        cases=[None,[],{},dict(defaults(),groups=[]),dict(defaults(),groups={}),dict(defaults(),features={})]
        for value in ('NaN','Infinity',True,'bad','-1','1e-50'):
            cfg=defaults();cfg['groups']['macro']=value;cases.append(cfg)
        cfg=defaults();cfg['groups']={g:'0' for g in GROUPS};cases.append(cfg)
        for key,value in [('enabled','on'),('weight','-1'),('good','-10'),('max_age',0),('max_age',False)]:
            cfg=defaults();cfg['features']['revenue_growth'][key]=value;cases.append(cfg)
        cfg=defaults();cfg['features']['pe']={};cases.append(cfg)
        cfg=defaults()
        for f in cfg['features'].values(): f['enabled']=False
        cases.append(cfg)
        for rule_change in [dict(profile='bad'),dict(enabled=1),dict(phases={}),dict(sensitivity=[]),dict(not_applicable=['bad']),dict(not_applicable=['pe','pe']),dict(bounds=[]),dict(bounds={'pe':dict(bad='1',good='1')})]:
            cfg=defaults();cfg['sectors']['1']=dict(profile_rule(),**rule_change);cases.append(cfg)
        for sid in ('0','01','unknown','2'):
            cfg=defaults();cfg['sectors'][sid]=profile_rule();cases.append(cfg)
        for cfg in cases:
            with self.subTest(cfg=cfg),self.assertRaises(RecordError): validate(cfg,SECTORS)

    def test_observation_validation_handles_units_dates_population_and_quality(self):
        base={k:v for k,v in observation('pe',10).items() if k not in ('id','received_at','supersedes_id')}
        self.assertEqual(Observation(**base).validated(CUTOFF)['value'],'10')
        cases=[dict(sector_id=True),dict(metric='phase'),dict(metric='bad'),dict(unit='RUB'),dict(status='bad'),
               dict(period_start='2027-01-01'),dict(period_end='2027-01-01'),dict(sample_size=0),dict(population_size=1),dict(sample_size=True),
               dict(published_at='2026-10-01'),dict(published_at='2026-11-27'),dict(published_at='2026-11-26T15:00:00Z'),
               dict(source=''),dict(methodology=''),dict(source_url='javascript:alert(1)'),dict(value='NaN'),dict(status='unavailable'),
               dict(metric='liquidity',unit='million_rub',value='-1'),dict(metric='currency',unit='score',value='101')]
        for change in cases:
            with self.subTest(change=change),self.assertRaises((RecordError,ValueError)):
                Observation(**dict(base,**change)).validated(CUTOFF)
        withdrawn=Observation(**dict(base,status='unavailable',value=None,sample_size=0)).validated(CUTOFF)
        self.assertIsNone(withdrawn['value'])
        self.assertEqual(Observation(**dict(base,published_at='2026-11-26')).validated(CUTOFF)['published_at'],'2026-11-26T23:59:59.999999+00:00')


if __name__=='__main__': unittest.main()
