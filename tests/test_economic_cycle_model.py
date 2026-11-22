"""Independent economic vignettes, missingness, scoring invariants and persistence."""
from copy import deepcopy
from decimal import Decimal as D, localcontext
import json
from pathlib import Path
import unittest
from wealth_manager.modules.economic_cycle.config import defaults,validate,FEATURES,GROUPS,compatible,shares
from wealth_manager.modules.economic_cycle.model import assess,score,criteria,PHASES,DESCRIPTIONS,REASONS,TRANSITIONS,CRITERIA
from wealth_manager.shared.records import RecordError

# Synthetic economic scenarios: GDP, inflation, rates, spreads, market returns.
SCENARIOS = {
 'RECOVERY': '-1 .7 -2 .9 1 .2 | 3.5 -.6 2 3 -.7 | -1.5 2 10 | .8 .8 .4 | 8 4 -1',
 'EARLY_EXPANSION': '3 .8 -.2 .4 4 .6 | 4.2 .05 4.2 4.1 .1 | -.25 2.2 8 | .9 .8 .05 | 7 1 -.25',
 'LATE_EXPANSION': '3 .4 2 -.2 2 .3 | 6.5 .5 6.5 6 .8 | 1.5 4 12 | .1 .1 -.3 | 2 -2 1.5',
 'SLOWDOWN': '.5 -.3 .5 -.9 -.5 -.3 | 5.5 -.2 5 5 0 | .3 4.5 12 | -.5 -.5 -.15 | -6 .5 .2',
 'RECESSION': '-3 -.8 -2.5 -.5 -4 -.7 | 3.7 -.6 2.5 3.5 -1 | -1 3 10 | 0 0 .2 | -7 3 -1',
}

def evidence(phase='EARLY_EXPANSION',period='2026-09-30'):
    numbers=map(D,SCENARIOS[phase].replace('|','').split())
    return {k:dict(value=v,date=period,revisions=[i+1],reason='',roles=[],note='') for i,(k,v) in enumerate(zip(FEATURES,numbers))}

def missing(e,codes):
    for k in codes: e[k].update(value=None,date=None,reason='short',revisions=[])
    return e


class CycleModelTests(unittest.TestCase):
    def test_five_independent_economic_scenarios_and_exact_contributions(self):
        cfg=validate(defaults())
        for phase in SCENARIOS:
            with self.subTest(phase=phase):
                r=assess(evidence(phase),evidence(phase,'2026-06-30'),cfg)
                self.assertEqual(r['phase'],phase);self.assertEqual(r['transition'],'confirmed');self.assertEqual(r['coverage'],100)
                self.assertGreater(r['confidence'],60);self.assertLessEqual(r['confidence'],100)
                self.assertAlmostEqual(sum(r['group_contributions'].values()),r['scores'][phase],places=25)
                self.assertTrue(r['supports']);self.assertTrue(all(0<=v<=100 for v in r['scores'].values()))

    def test_missing_not_zero_and_no_single_group_or_market_only_phase(self):
        cfg=validate(defaults()); e=evidence(); missing(e,['gdp_qoq','output_gap','gdp_accel','gdp_yoy'])
        r=score(e,cfg);self.assertEqual(r['phase'],'INSUFFICIENT_DATA');self.assertEqual(r['reason'],'activity');self.assertEqual(r['coverage'],64)
        e=missing(evidence(),[k for k,v in FEATURES.items() if v[0]!='markets']);r=score(e,cfg)
        self.assertEqual(r['coverage'],10);self.assertEqual(r['reason'],'coverage')
        e=missing(evidence(),['cpi_yoy']);r=score(e,cfg);self.assertEqual(r['coverage'],94);self.assertIsNone(r['features']['cpi_yoy']['normalized'])
        cfg['groups']={g:('100' if g=='activity' else '0') for g in GROUPS};r=score(evidence(),validate(cfg))
        self.assertEqual(r['reason'],'groups')

    def test_disable_differs_from_missing_and_correlated_group_is_capped(self):
        cfg=defaults();cfg['features']['cpi_saar']['enabled']=False;cfg=validate(cfg)
        e=evidence();e['cpi_saar']['value']=D(1000);r=score(e,cfg)
        self.assertEqual(r['coverage'],100);self.assertIsNone(r['features']['cpi_saar']['normalized'])
        self.assertEqual(r['covered']['inflation'],20)
        e['cpi_yoy']['value']=D(100000);r=score(e,cfg)
        self.assertEqual(r['features']['cpi_yoy']['normalized'],1)
        self.assertLessEqual(r['contributions']['RECESSION']['inflation'],20)

    def test_confirmation_needs_new_gdp_period_and_missing_data_never_carries_phase(self):
        cfg=validate(defaults());cur=evidence();prev=evidence()
        r=assess(cur,prev,cfg);self.assertEqual(r['transition'],'provisional')
        confirmed=assess(cur,evidence(period='2026-06-30'),cfg)
        self.assertLess(r['confidence'],confirmed['confidence'])
        r=assess(evidence('RECESSION'),evidence('LATE_EXPANSION','2026-06-30'),cfg)
        self.assertEqual(r['phase'],'RECESSION');self.assertEqual(r['transition'],'transition')
        r=assess(missing(cur,list(FEATURES)),prev,cfg)
        self.assertEqual(r['transition'],'insufficient');self.assertEqual(r['confidence'],0)
        self.assertEqual(r['phase'],'INSUFFICIENT_DATA')

    def test_contradictions_explain_disagreement_without_inventing_data(self):
        e=evidence();e['bonds']['value']=D(-5)
        r=assess(e,evidence(period='2026-06-30'),validate(defaults()))
        self.assertEqual(r['phase'],'EARLY_EXPANSION');self.assertIn('bonds',r['contradictions'])
        self.assertNotIn('bonds',r['supports'])

    def test_raw_activity_gates_prevent_profile_only_classification(self):
        cfg=validate(defaults());e=evidence('RECOVERY');e['gdp_yoy']['value']=D(5);e['gdp_qoq']['value']=D(-1);e['gdp_accel']['value']=D(-1);e['output_gap']['value']=D(2)
        self.assertFalse(criteria({k:v['value'] for k,v in e.items()})['RECOVERY'])
        # Centers cannot make positive raw GDP satisfy the recession condition.
        e=evidence('RECESSION');e['gdp_yoy']['value']=D(1);e['gdp_qoq']['value']=D(1)
        cfg['features']['gdp_yoy']['center']='4';cfg['features']['gdp_qoq']['center']='2'
        r=score(e,cfg);self.assertEqual(r['reason'],'criteria');self.assertEqual(r['phase'],'INSUFFICIENT_DATA')

    def test_config_normalization_is_idempotent_exact_and_context_independent(self):
        cfg=defaults();cfg['groups']={g:'1' for g in GROUPS};cfg['features']['gdp_yoy']['weight']='17'
        with localcontext() as ctx:
            ctx.prec=6;normalized=validate(cfg)
        self.assertEqual(normalized,validate(normalized));self.assertEqual(sum(map(D,normalized['groups'].values())),100)
        for g in GROUPS:self.assertEqual(sum(D(normalized['features'][k]['weight']) for k,v in FEATURES.items() if v[0]==g),100)
        self.assertEqual(shares({'x':D(0)}),{'x':D(0)})
        r=assess(evidence(),evidence(),normalized)
        with localcontext() as ctx:
            ctx.prec=6;self.assertEqual(r,assess(evidence(),evidence(),normalized))

    def test_invalid_config_and_role_bindings_fail_before_mutation(self):
        cases=[None,[],{},dict(defaults(),series=[]),dict(defaults(),groups={})]
        for value in ('-1','NaN','Infinity',True,'bad'):
            cfg=defaults();cfg['groups']['activity']=value;cases.append(cfg)
        for key,value in [('scale','0'),('center','NaN'),('enabled','on'),('weight','-1')]:
            cfg=defaults();cfg['features']['gdp_yoy'][key]=value;cases.append(cfg)
        cfg=defaults();cfg['groups']={g:0 for g in GROUPS};cases.append(cfg)
        cfg=defaults()
        for k in FEATURES:cfg['features'][k]['enabled']=False
        cases.append(cfg)
        for value in (0,-1,True,'1'):
            cfg=defaults();cfg['series']['gdp']=value;cases.append(cfg)
        cases.extend([dict(defaults(),window=19),dict(defaults(),real_rate_basis='actual_cpi')])
        for cfg in cases:
            with self.subTest(cfg=cfg),self.assertRaises(RecordError):validate(cfg)
        cfg=defaults();cfg['series']['gdp']=1
        for cat in ({},{1:dict(indicator='key_rate',country='RU',frequency='daily',unit='percent',adjustment='nsa')}):
            with self.assertRaises(RecordError):validate(cfg,cat)
        self.assertFalse(compatible('gdp',dict(indicator='gdp_real_level_sa',country='US',frequency='quarterly',unit='RUB',adjustment='sa')))

    def test_every_explanation_has_an_english_translation(self):
        from wealth_manager.modules.economic_cycle.config import ROLE_LABELS
        from wealth_manager.modules.economic_cycle.signals import REASONS as SIGNAL_REASONS
        locale=json.loads(Path('wealth_manager/adapters/web/locales/en.json').read_text())
        messages=list(GROUPS.values())+list(ROLE_LABELS.values())+[v[1] for v in FEATURES.values()]
        for d in (PHASES,DESCRIPTIONS,REASONS,TRANSITIONS,CRITERIA,SIGNAL_REASONS):messages+=list(d.values())
        for s in messages:self.assertTrue(s in locale,s)


    def test_close_candidates_abstain_and_small_phase_advantage_is_held(self):
        cfg=validate(defaults());early=evidence();late=evidence('LATE_EXPANSION')
        def mixed(fraction):
            return {k:dict(v,value=v['value']*(1-fraction)+late[k]['value']*fraction) for k,v in early.items()}
        ambiguous=assess(mixed(D('.5')),evidence(period='2026-06-30'),cfg)
        self.assertEqual(ambiguous['phase'],'INSUFFICIENT_DATA');self.assertEqual(ambiguous['reason'],'ambiguous')
        held=assess(mixed(D('.55')),evidence(period='2026-06-30'),cfg)
        self.assertEqual(held['phase'],'EARLY_EXPANSION');self.assertEqual(held['candidate'],'LATE_EXPANSION')
        self.assertEqual(held['transition'],'held');self.assertTrue(3<=held['margin']<5)
        self.assertAlmostEqual(sum(held['group_contributions'].values()),held['scores']['EARLY_EXPANSION'],places=25)
        self.assertLess(held['confidence'],25)
        # A partial, otherwise clear scenario remains explainable, not imputed.
        partial=assess(missing(evidence(),['cpi_saar','core_yoy']),evidence(period='2026-06-30'),cfg)
        self.assertEqual(partial['phase'],'EARLY_EXPANSION');self.assertEqual(partial['coverage'],93)
        self.assertNotIn('core_yoy',partial['supports']+partial['contradictions'])
