"""HTTP forms for independent sector settings, source evidence and scored views."""
from copy import deepcopy
from flask import request, render_template, redirect, url_for, session
from wealth_manager.modules.sector_scoring.config import GROUPS, METRICS, AUTO, PROFILES, UNITS, EXCLUSIONS, profile_rule
from wealth_manager.modules.sector_scoring.domain import Observation, STATUSES
from wealth_manager.modules.sector_scoring.model import REASONS
from wealth_manager.modules.economic_cycle.model import PHASES
from wealth_manager.modules.market_catalog.domain import find
from wealth_manager.shared.decimal import exact_text, rounded
from .commands import validated


def ranking(result,parent_id=None):
    return sorted((r for r in result['sectors'] if r['sector']['parent_id']==parent_id),
                  key=lambda r:(r['rank'] is None,r['rank'] or 0,r['sector']['id']))


def register_sector_scoring(app,runtime):
    service=runtime.services['sector_scoring']
    app.jinja_env.filters['sector_number']=lambda value:exact_text(rounded(value,2))
    common=dict(sector_groups=GROUPS,metrics=METRICS,auto_metrics=AUTO,profiles=PROFILES,sector_units=UNITS,sector_reasons=REASONS,phases=PHASES,statuses=STATUSES)
    def token(): return session.get('vault')
    def params(values): return dict(as_of=values.get('as_of') or None,mode=values.get('mode') or 'known')
    def render(preview,sid=None,saved_id=None):
        result=preview['result'];detail=None
        if sid is not None:
            find([r['sector'] for r in result['sectors']],sid)
            detail=next(r for r in result['sectors'] if r['sector']['id']==sid)
        return render_template('sector_scoring.html',**preview,**common,rows=ranking(result,sid),detail=detail,saved_id=saved_id)

    @app.get('/macroeconomics/sectors')
    @validated
    def sector_scores(): return render(service.preview(token(),**params(request.args)))

    @app.get('/macroeconomics/sectors/<int:sid>')
    @validated
    def sector_score_detail(sid): return render(service.preview(token(),**params(request.args)),sid)

    @app.post('/macroeconomics/sectors/save')
    @validated
    def sector_score_save():
        rid=service.save(token(),expected_fingerprint=request.form.get('fingerprint',''),**params(request.form))
        return redirect(url_for('sector_score_saved',rid=rid))

    @app.get('/macroeconomics/sectors/runs/<int:rid>')
    @validated
    def sector_score_saved(rid): return render(dict(result=service.saved(token(),rid)),saved_id=rid)

    @app.get('/macroeconomics/sectors/runs/<int:rid>/<int:sid>')
    @validated
    def sector_score_saved_detail(rid,sid): return render(dict(result=service.saved(token(),rid)),sid,saved_id=rid)

    @app.post('/macroeconomics/sectors/<int:sid>/observations')
    @validated
    def sector_observation_save(sid):
        form=request.form;metric=form.get('metric','')
        observation=Observation(sid,metric,form.get('value') or None,METRICS.get(metric,('','',''))[2],
                                form.get('period_start',''),form.get('period_end',''),form.get('source',''),form.get('methodology',''),
                                int(form.get('sample_size','')),int(form.get('population_size','')),form.get('published_at',''),
                                form.get('source_url',''),form.get('status',''))
        service.observe(token(),observation,int(form.get('revision_token','')))
        return redirect(url_for('sector_score_detail',sid=sid))

    @app.get('/settings/sector-scoring')
    @validated
    def sector_scoring_settings(): return render_template('sector_scoring_settings.html',**service.settings(token()),**common,sector=None)

    @app.post('/settings/sector-scoring')
    @validated
    def sector_scoring_configure():
        form=request.form;restore=form.get('action')=='restore';cfg=service.settings(token())['config']
        if not restore:
            cfg['groups']={g:form.get('group_'+g,'') for g in GROUPS}
            for k in ('min_coverage','min_sample','min_liquidity'): cfg[k]=form.get(k,'')
            for code,f in cfg['features'].items():
                f.update(enabled=form.get('enabled_'+code)=='on',weight=form.get('weight_'+code,''))
                if code!='phase': f.update(bad=form.get('bad_'+code,''),good=form.get('good_'+code,''))
                if code not in AUTO: f['max_age']=int(form.get('max_age_'+code,''))
        service.configure(token(),cfg,int(form.get('config_id','')),restore)
        return redirect(url_for('sector_scores'))

    @app.get('/settings/sector-scoring/<int:sid>')
    @validated
    def sector_scoring_sector_settings(sid):
        state=service.settings(token());sector=find(state['sectors'],sid);rule=state['rules'][sid]
        return render_template('sector_scoring_settings.html',**state,**common,sector=sector,rule=rule,profile_exclusions=EXCLUSIONS[rule['profile']])

    @app.post('/settings/sector-scoring/<int:sid>')
    @validated
    def sector_scoring_sector_configure(sid):
        form=request.form;state=service.settings(token());find(state['sectors'],sid);cfg=state['config']
        if form.get('action')=='inherit': cfg['sectors'].pop(str(sid),None)
        else:
            profile=form.get('profile','')
            if profile not in PROFILES: raise ValueError()
            rule=profile_rule(profile) if form.get('action')=='profile' else deepcopy(state['rules'][sid])
            if form.get('action')!='profile':
                rule.update(profile=profile,enabled=form.get('sector_enabled')=='on',
                            phases={p:form.get('phase_'+p,'') for p in rule['phases']},
                            sensitivity={k:form.get('sensitivity_'+k,'') for k in rule['sensitivity']},
                            not_applicable=form.getlist('not_applicable'),
                            bounds={k:dict(bad=form.get('bad_'+k,''),good=form.get('good_'+k,'')) for k in METRICS if form.get('override_'+k)=='on'})
            cfg['sectors'][str(sid)]=rule
        service.configure(token(),cfg,int(form.get('config_id','')))
        return redirect(url_for('sector_scoring_sector_settings',sid=sid))
