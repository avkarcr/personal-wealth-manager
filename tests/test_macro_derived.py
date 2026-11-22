"""M6: mathematical oracles, calendar semantics and absence of look-ahead."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal as D, localcontext
from fractions import Fraction as F
from copy import deepcopy
import unittest
from wealth_manager.modules.macroeconomics.derived import calculate, options, hp_trend, log_linear_endpoint, minus_months, LABELS, MESSAGES
from wealth_manager.shared.records import RecordError


def row(code='gdp_real_level_sa',n=0,value='100',**kw):
    year,quarter=2018+n//4,n%4
    start=date(year,quarter*3+1,1);end=date(year,quarter*3+3,monthrange(year,quarter*3+3)[1])
    r=dict(series_id=1,source_id=1,revision_id=n+1,indicator=code,country='RU',unit='RUB',scale=D(1),frequency='quarterly',adjustment='sa',base_year=2021,methodology='test-v1',period_start=str(start),period_end=str(end),value=D(value),publication_precision='date',status='final',available_at=str(end)+'T00:00:00.000000+00:00')
    r.update(kw);return r


def monthly(code='cpi_mom_index',count=13,value='101',**kw):
    result=[]
    for i in range(count):
        y,m=2023+i//12,i%12+1
        result.append(row(code,i,value,unit='index',frequency='monthly',adjustment='nsa',base_year=None,period_start=f'{y}-{m:02d}-01',period_end=str(date(y,m,monthrange(y,m)[1])),**kw))
    return result


def daily(code,days,values,**kw):
    return [row(code,i,v,unit='index' if code.startswith('moex_') and code!='moex_rusfar' else 'percent',frequency='daily',adjustment='nsa',base_year=None,period_start=d,period_end=d,**kw) for i,(d,v) in enumerate(zip(days,values))]


def calc(rows,other=None,cutoff='2026-11-22T23:59:59+00:00',**kw):
    return calculate(rows,other or [],options(**kw),cutoff,'known')


def metrics(result):return {m['code']:m for m in result['metrics']}


class DerivedTests(unittest.TestCase):
    def test_hp_matches_independent_exact_dense_oracle_and_linear_trend(self):
        y=list(map(D,['0','0','1','0','0']));n=len(y);s=1600
        # Independent dense Gauss-Jordan oracle, rational arithmetic.
        k=[[F(int(j==i)-2*int(j==i+1)+int(j==i+2)) for j in range(n)] for i in range(n-2)]
        a=[[F(i==j)+s*sum(k[t][i]*k[t][j] for t in range(n-2)) for j in range(n)]+[F(y[i])] for i in range(n)]
        for i in range(n):
            divisor=a[i][i];a[i]=[v/divisor for v in a[i]]
            for j in range(n):
                if j!=i:
                    factor=a[j][i];a[j]=[v-factor*w for v,w in zip(a[j],a[i])]
        with localcontext() as c:
            c.prec=50;actual=hp_trend(y)
            for got,expected in zip(actual,[r[-1] for r in a]):self.assertLess(abs(got-D(expected.numerator)/D(expected.denominator)),D('1e-40'))
            linear=list(map(D,range(30)));self.assertLess(max(abs(a-b) for a,b in zip(hp_trend(linear),linear)),D('1e-38'))
        for values,smoothing in [([D(1)],D(1600)),(y,D(0))]:
            with self.assertRaises(RecordError):hp_trend(values,smoothing)
        self.assertEqual(log_linear_endpoint([D(1),D(2),D(3)]),3)

    def test_growth_units_acceleration_and_saar_compounding(self):
        data=monthly();m=metrics(calc(data))
        self.assertEqual(m['growth']['points'][0]['value'],1)
        self.assertEqual(m['yoy']['points'][-1]['value'],(D('1.01')**12-1)*100)
        self.assertEqual(m['saar']['reason'],'metadata')
        sa=[dict(r,indicator='cpi_mom_sa_index',adjustment='sa') for r in data]
        m=metrics(calc(sa));self.assertEqual(m['saar']['points'][-1]['value'],(D('1.01')**12-1)*100)
        self.assertEqual(m['acceleration']['points'][-1]['value'],0)
        pc=[dict(r,indicator='cpi_mom_sa_growth',unit='percent',value=D(1)) for r in sa]
        self.assertEqual(metrics(calc(pc))['saar']['points'],m['saar']['points'])
        pc[1]['value']=D('1.5');self.assertEqual(metrics(calc(pc))['acceleration']['points'][0]['value'],D('.5'))
        scaled=[dict(r,value=D('10.1'),scale=D(10)) for r in data]
        self.assertEqual(metrics(calc(scaled))['yoy']['points'],metrics(calc(data))['yoy']['points'])

    def test_gdp_yoy_qoq_scaling_and_exact_provenance(self):
        rows=[row(n=i,value=100+i*10) for i in range(6)];m=metrics(calc(rows))
        self.assertEqual(m['yoy']['points'][0]['value'],40)
        self.assertEqual(m['qoq']['points'][0]['value'],10)
        self.assertEqual(m['acceleration']['points'][-1]['revisions'],[1,2,5,6])
        self.assertEqual(m['gap']['reason'],'short')
        annual=[dict(row(n=i,value=100+i*10),frequency='annual',period_start=f'{2018+i}-01-01',period_end=f'{2018+i}-12-31') for i in range(3)]
        m=metrics(calc(annual));self.assertEqual(m['yoy']['points'][0]['value'],10);self.assertEqual(m['qoq']['reason'],'metadata')

    def test_gap_methods_constant_exponential_scale_invariance_and_future_append(self):
        with localcontext() as c:
            c.prec=50;rows=[row(n=i,value=D(100)*D('1.01')**i) for i in range(28)]
        for method in ('hp','log_linear'):
            m=metrics(calc(rows,method=method));self.assertEqual(len(m['gap']['points']),9)
            self.assertLess(max(abs(p['value']) for p in m['gap']['points']),D('1e-35'))
        rows[-1]['value']*=D('1.1');m=metrics(calc(rows));self.assertGreater(m['gap']['points'][-1]['value'],0)
        alternate=metrics(calc(rows,method='log_linear'))
        self.assertAlmostEqual(m['sensitivity']['points'][-1]['value'],m['gap']['points'][-1]['value']-alternate['gap']['points'][-1]['value'],places=24)
        scaled=metrics(calc([dict(r,scale=D(1000000000)) for r in rows]))
        self.assertAlmostEqual(scaled['gap']['points'][-1]['value'],m['gap']['points'][-1]['value'],places=24)
        self.assertAlmostEqual(scaled['potential']['points'][-1]['value']/D(1000000000),m['potential']['points'][-1]['value'],places=24)
        extended=metrics(calc(rows+[row(n=28,value=10000)]));self.assertEqual(extended['gap']['points'][:-1],m['gap']['points'])
        self.assertEqual(m['gap']['points'][-1]['revisions'],list(range(1,29)))
        long=metrics(calc([row(n=i,value=100+i) for i in range(45)],window=20))
        self.assertEqual(long['gap']['points'][-1]['revisions'],list(range(26,46)))

    def test_gaps_short_non_sa_unknown_method_and_invalid_levels(self):
        rows=[row(n=i,value=100+i) for i in range(24)]
        broken=rows[:10]+rows[11:];result=calc(broken)
        self.assertIn('gaps',result['warnings']);self.assertEqual(metrics(result)['gap']['points'],[])
        recovered=calc([row(n=i,value=100+i) for i in range(40) if i!=10])
        self.assertEqual(metrics(recovered)['gap']['points'][0]['revisions'],list(range(12,32)))
        cpi=monthly();cpi.pop(6);m=metrics(calc(cpi));self.assertEqual(m['yoy']['points'],[])
        for changes,reason in [(dict(adjustment='nsa'),'metadata')]:
            m=metrics(calc([dict(r,**changes) for r in rows]));self.assertEqual(m['gap']['reason'],reason)
        for changes in (dict(base_year=None),dict(unit='USD'),dict(methodology='unknown'),dict(adjustment='unknown')):
            self.assertEqual(calc([dict(r,**changes) for r in rows])['reason'],'metadata')
        self.assertEqual(calc([dict(r,value=D(0)) for r in rows])['reason'],'nonpositive')
        self.assertEqual(calc([dict(r,indicator='gdp_nominal_level') for r in rows])['reason'],'unsupported')
        for kw in (dict(method='wrong'),dict(window=19),dict(window=121),dict(hold_rate='on')):
            with self.assertRaises(RecordError):options(**kw)

    def test_calendar_returns_horizons_weekends_and_no_forward_anchor(self):
        dates=['2023-02-28','2023-08-29','2023-11-29','2024-01-29','2024-02-29']
        rows=daily('moex_mcftr',dates,[100,110,120,125,150]);m=metrics(calc(rows))
        self.assertEqual(m['return_1m']['points'][-1]['value'],20)
        self.assertEqual(m['return_3m']['points'][-1]['value'],25)
        self.assertEqual(m['return_12m']['points'][-1]['value'],50)
        self.assertEqual(m['return_12m']['points'][-1]['revisions'],[1,5])
        self.assertEqual(minus_months(date(2024,3,31),1),date(2024,2,29))
        rows=daily('moex_imoex',['2024-01-23','2024-02-02','2024-02-29'],[100,110,150])
        self.assertEqual(metrics(calc(rows))['return_1m']['points'][-1]['value'],50)
        rows[0].update(period_start='2024-01-01',period_end='2024-01-01')
        self.assertFalse(any(p['date']=='2024-02-29' for p in metrics(calc(rows))['return_1m']['points']))
        rows[-1]['value']=D(0);self.assertEqual(calc(rows)['reason'],'nonpositive')

    def test_spreads_match_dates_source_methodology_shape_and_changes(self):
        days=['2023-11-29','2024-02-29'];a=daily('ofz_zero_10y',days,[10,9]);b=daily('ofz_zero_2y',days,[9,10],series_id=2)
        b=[dict(r,revision_id=r['revision_id']+10) for r in b];m=metrics(calc(a,b))
        self.assertEqual([p['value'] for p in m['spread']['points']],[1,-1]);self.assertEqual([p['note'] for p in m['spread']['points']],['normal','inverted'])
        self.assertEqual(m['spread_3m']['points'][-1]['value'],-2);self.assertEqual(m['spread_3m']['points'][-1]['revisions'],[1,2,11,12])
        b[-1]['value']=D('8.9');self.assertEqual(metrics(calc(a,b))['spread']['points'][-1]['note'],'flat')
        self.assertEqual(metrics(calc(a))['spread']['reason'],'missing_pair')
        for changes in (dict(source_id=2),dict(country='US'),dict(methodology='other'),dict(indicator='key_rate')):
            self.assertEqual(metrics(calc(a,[dict(r,**changes) for r in b]))['spread']['reason'],'metadata')
        b=daily('ofz_zero_1y',['2024-01-01'],[10],series_id=2)
        self.assertEqual(metrics(calc(a,b))['spread']['reason'],'no_overlap')

    def test_ex_ante_fisher_horizon_assumption_age_and_no_realized_substitution(self):
        rate=daily('key_rate',['2024-02-29'],[16]);forecast=monthly('inflation_expectations_12m',count=2,value=8,series_id=2)
        forecast=[dict(r,unit='percent') for r in forecast]
        # Shift the last forecast to the month preceding the nominal rate.
        forecast=[dict(forecast[-1],period_start='2024-02-01',period_end='2024-02-29',revision_id=20)]
        cutoff='2024-03-01T12:00:00+00:00'
        self.assertEqual(metrics(calc(rate,forecast,cutoff))['real_rate']['reason'],'assumption')
        m=metrics(calc(rate,forecast,cutoff,hold_rate=True));self.assertAlmostEqual(m['real_rate']['points'][0]['value'],(D('1.16')/D('1.08')-1)*100,places=24)
        self.assertEqual(m['real_rate']['points'][0]['revisions'],[1,20])
        self.assertEqual(m['real_rate']['points'][0]['date'],'2024-03-01')
        self.assertEqual(metrics(calc(rate,[dict(forecast[0],indicator='cpi_yoy_growth')],cutoff,hold_rate=True))['real_rate']['reason'],'expectations')
        self.assertEqual(metrics(calc(rate,forecast,hold_rate=True))['real_rate']['reason'],'stale')
        nominal=[dict(rate[0],indicator='ofz_zero_1y')];self.assertTrue(metrics(calc(nominal,forecast,cutoff))['real_rate']['points'])
        self.assertEqual(metrics(calc(rate))['real_rate']['reason'],'missing_pair')

    def test_rate_changes_bp_and_warnings(self):
        rows=daily('key_rate',['2024-01-01','2024-01-02','2024-03-01'],[16,15,14]);rows[1].update(publication_precision='unknown',status='revised')
        result=calc(rows);self.assertEqual(metrics(result)['change_bp']['points'][0]['value'],-100);self.assertEqual(len(metrics(result)['change_bp']['points']),1)
        self.assertTrue({'stale','unknown_publication','revised'}<=set(result['warnings']))
        self.assertEqual(calculate([],[],options(),'2024-01-01T00:00:00Z','latest')['warnings'],['latest'])

    def test_invalid_metadata_duplicate_periods_and_budget(self):
        rows=monthly(count=3)
        for bad in ([rows[0],rows[0]],[rows[0],dict(rows[1],series_id=2)]):
            with self.assertRaises(RecordError):calc(bad)
        with self.assertRaises(RecordError):calc(rows*3334)
        for code,unit,freq in [('cpi_mom_index','RUB','monthly'),('gdp_real_qoq_sa_growth','percent','annual'),('moex_imoex','percent','daily'),('key_rate','index','daily')]:
            source=daily(code,['2024-01-01'],[100]) if freq=='daily' else [dict(row(code),frequency='annual',period_start='2024-01-01',period_end='2024-12-31')] if freq=='annual' else rows[:1]
            self.assertEqual(calc([dict(r,indicator=code,unit=unit) for r in source])['reason'],'metadata')
        for code,value in [('cpi_mom_index',0),('cpi_mom_growth',-100)]:
            self.assertEqual(calc([dict(rows[0],indicator=code,unit='index' if code.endswith('index') else 'percent',value=D(value))])['reason'],'nonpositive')
        self.assertEqual(calc(daily('key_rate',['2024-01-01'],[-100]))['reason'],'nonpositive')

    def test_all_dynamic_labels_are_localized(self):
        from wealth_manager.adapters.web.i18n import EN
        for label in list(LABELS.values())+list(MESSAGES.values()):self.assertIn(label,EN)
