"""Temporal alignment and source semantics at the M6 -> M7 boundary."""
from decimal import Decimal as D
from copy import deepcopy
from datetime import date,timedelta
import unittest
from test_macro_derived import row,monthly,daily
from wealth_manager.modules.economic_cycle.config import defaults,validate
from wealth_manager.modules.economic_cycle.signals import prepare


def run(groups,cutoff='2024-02-20',**options):
    cfg=defaults();cfg.update(options);rows=[]
    for sid,(role,data) in enumerate(groups.items(),1):
        cfg['series'][role]=sid
        rows.extend(dict(r,series_id=sid,revision_id=sid*1000+i+1) for i,r in enumerate(data))
    return prepare(rows,validate(cfg),cutoff+'T23:59:59+00:00','known')


class CycleSignalsTests(unittest.TestCase):
    def test_cpi_acceleration_uses_annual_not_monthly_growth_and_tracks_inputs(self):
        cpi=monthly(count=13);cpi[-1]['value']=D(102)
        e,_=run({'cpi':cpi})
        expected=((D('1.01')**11*D('1.02'))-1)*100
        self.assertAlmostEqual(e['cpi_yoy']['value'],expected,places=24)
        self.assertAlmostEqual(e['cpi_accel']['value'],expected-(D('1.01')**12-1)*100,places=24)
        self.assertEqual(len(e['cpi_accel']['revisions']),13)
        self.assertEqual(e['cpi_accel']['date'],'2024-01-31')
        # Latest source period is present, but the 12-month chain is broken.
        e,_=run({'cpi':cpi[:2]+cpi[3:]})
        self.assertIsNone(e['cpi_yoy']['value']);self.assertIsNone(e['cpi_accel']['value'])

    def test_stale_values_and_older_derived_point_are_excluded(self):
        data=monthly('cpi_yoy_index',count=2)
        e,_=run({'cpi':data},'2024-01-31');self.assertEqual(e['cpi_yoy']['reason'],'stale')
        data=monthly('cpi_yoy_index',count=4);del data[2]
        e,_=run({'cpi':data},'2023-05-10')
        self.assertEqual(e['cpi_accel']['reason'],'lag');self.assertIsNone(e['cpi_accel']['value'])
        e,w=run({'cpi':[dict(r,methodology='unknown') for r in data]},'2023-05-10')
        self.assertEqual(e['cpi_yoy']['reason'],'metadata')

    def test_curve_dates_sources_and_exante_horizon_are_checked(self):
        days=['2023-11-16','2024-02-16']
        ten=daily('ofz_zero_10y',days,[10,12]);two=daily('ofz_zero_2y',days,[9,10]);one=daily('ofz_zero_1y',days,[8,9])
        exp=monthly('inflation_expectations_12m',count=13)
        exp=[dict(r,unit='percent',value=D(6)) for r in exp]
        e,_=run(dict(yield_10y=ten,yield_2y=two,yield_1y=one,expectations=exp))
        self.assertEqual(e['spread_2']['value'],2);self.assertEqual(e['curve_change']['value'],1)
        self.assertEqual(len(e['curve_change']['revisions']),4)
        self.assertAlmostEqual(e['real_rate']['value'],(D('1.09')/D('1.06')-1)*100,places=24)
        self.assertEqual(e['real_rate']['date'],'2024-02-20')
        e,_=run(dict(yield_10y=ten,yield_2y=[dict(r,source_id=2) for r in two]))
        self.assertEqual(e['spread_2']['reason'],'metadata')
        extra=daily('ofz_zero_10y',['2024-02-19'],[13])[0]
        e,_=run(dict(yield_10y=ten+[extra],yield_2y=two));self.assertEqual(e['spread_2']['reason'],'lag')
        rates=[dict(r,indicator='key_rate') for r in one]
        e,_=run(dict(key_rate=rates,expectations=exp),real_rate_basis='key_rate')
        self.assertEqual(e['real_rate']['reason'],'assumption')
        e,_=run(dict(key_rate=rates,expectations=exp),real_rate_basis='key_rate',hold_rate=True)
        self.assertIsNotNone(e['real_rate']['value']);self.assertEqual(e['real_rate']['note'],'hold_key_rate_12m')

    def test_raw_signals_calendar_changes_missing_periods_and_quality(self):
        data=monthly('unemployment_rate',count=4)
        data=[dict(r,unit='percent',value=D(6)-D(i)/10) for i,r in enumerate(data)]
        e,_=run(dict(unemployment=data),'2023-05-10');self.assertEqual(e['employment']['value'],D('.3'))
        e,_=run(dict(unemployment=data[:1]+data[2:]),'2023-05-10');self.assertIsNone(e['employment']['value'])
        data[-1]['value']=D(-1);e,_=run(dict(unemployment=data),'2023-05-10');self.assertEqual(e['employment']['reason'],'metadata')
        rates=daily('key_rate',['2023-11-16','2024-02-16'],[12,10])
        e,_=run(dict(key_rate=rates));self.assertEqual(e['key_change']['value'],-2);self.assertEqual(e['key_level']['value'],10)
        rates[-1]['value']=D(-101);e,_=run(dict(key_rate=rates));self.assertEqual(e['key_level']['reason'],'metadata')
        rates[-1].update(value=D(10),methodology='unknown');e,_=run(dict(key_rate=rates));self.assertEqual(e['key_change']['reason'],'metadata')

    def test_gdp_qoq_override_must_match_period_and_disabled_is_distinct(self):
        gdp=[row(n=i,value=100+i) for i in range(24)]
        quarter=[dict(gdp[-2],indicator='gdp_real_qoq_sa_growth',unit='percent',value=D(2),base_year=None)]
        e,_=run(dict(gdp=gdp,gdp_qoq=quarter));self.assertEqual(e['gdp_qoq']['reason'],'lag')
        quarter=[dict(gdp[-1],indicator='gdp_real_qoq_sa_growth',unit='percent',value=D(2),base_year=None)]
        e,_=run(dict(gdp=gdp,gdp_qoq=quarter));self.assertEqual(e['gdp_qoq']['value'],2)
        self.assertIsNotNone(e['output_gap']['value'])
        cfg=defaults();cfg['series']['gdp']=1;cfg['features']['gdp_yoy']['enabled']=False
        e,w=prepare(gdp,validate(cfg),'2024-02-20T00:00:00+00:00','latest')
        self.assertEqual(e['gdp_yoy']['reason'],'disabled');self.assertIn('latest',w)

    def test_unknown_publication_and_revision_warnings_are_not_hidden(self):
        data=monthly('cpi_yoy_index',count=13)
        data[-1].update(publication_precision='unknown',status='revised')
        e,w=run(dict(cpi=data))
        self.assertIn('unknown_publication',w);self.assertIn('revised',w)
        self.assertEqual(e['cpi_yoy']['value'],1)


    def test_long_history_is_bounded_without_losing_the_three_month_anchor(self):
        end=date(2026,11,20);count=11000
        dates=[str(end-timedelta(days=count-1-i)) for i in range(count)]
        data=daily('moex_imoex',dates,[100+i for i in range(count)])
        e,_=run(dict(equities=data),'2026-11-22')
        # Aug 20 -> Nov 20 spans exactly 92 calendar days in 2026.
        expected=(D(11099)/D(11007)-1)*100
        self.assertAlmostEqual(e['equities']['value'],expected,places=24)
        self.assertEqual(e['equities']['date'],'2026-11-20')
        self.assertEqual(e['equities']['revisions'],[11908,12000])
