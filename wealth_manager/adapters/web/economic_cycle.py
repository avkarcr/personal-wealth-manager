"""HTTP input/output for M7; all decisions live in the application and model."""
from flask import request, render_template, redirect, url_for, session
from wealth_manager.modules.economic_cycle.config import GROUPS, FEATURES, ROLES, ROLE_LABELS, compatible, defaults
from wealth_manager.modules.economic_cycle.model import PHASES, DESCRIPTIONS, REASONS, TRANSITIONS, CRITERIA
from wealth_manager.modules.economic_cycle.signals import REASONS as SIGNAL_REASONS
from wealth_manager.modules.macroeconomics.derived import MESSAGES
from .commands import validated


def register_economic_cycle(app,runtime):
    service=runtime.services['economic_cycle']
    common=dict(groups=GROUPS,features=FEATURES,phases=PHASES,descriptions=DESCRIPTIONS,reasons=REASONS,
                transitions=TRANSITIONS,criteria=CRITERIA,signal_reasons=SIGNAL_REASONS,messages=MESSAGES,roles=ROLE_LABELS)
    def parameters(values): return dict(as_of=values.get('as_of') or None,mode=values.get('mode') or 'known')

    @app.get('/macroeconomics/cycle')
    @validated
    def economic_cycle():
        return render_template('economic_cycle.html',**service.preview(session.get('vault'),**parameters(request.args)),**common,saved_id=None)

    @app.post('/macroeconomics/cycle')
    @validated
    def economic_cycle_save():
        rid=service.save(session.get('vault'),expected_fingerprint=request.form.get('fingerprint',''),**parameters(request.form))
        return redirect(url_for('economic_cycle_saved',rid=rid))

    @app.get('/macroeconomics/cycle/<int:rid>')
    @validated
    def economic_cycle_saved(rid):
        return render_template('economic_cycle.html',result=service.saved(session.get('vault'),rid),**common,saved_id=rid)

    @app.get('/settings/economic-cycle')
    @validated
    def economic_cycle_settings():
        state=service.settings(session.get('vault'))
        choices={role:[r for r in state['choices'] if compatible(role,r)] for role in ROLES}
        return render_template('economic_cycle_settings.html',config=state['config'],config_id=state['config_id'],choices=choices,**common)

    @app.post('/settings/economic-cycle')
    @validated
    def economic_cycle_configure():
        form=request.form; cfg=defaults(); restore=form.get('action')=='restore'
        if not restore:
            cfg.update(method=form.get('method'),window=int(form.get('window','')),hold_rate=form.get('hold_rate')=='on',real_rate_basis=form.get('real_rate_basis'))
            cfg['groups']={g:form.get('group_'+g,'') for g in GROUPS}
            cfg['series']={r:int(form['series_'+r]) if form.get('series_'+r) else None for r in ROLES}
            cfg['features']={f:dict(enabled=form.get('enabled_'+f)=='on',**{k:form.get(k+'_'+f,'') for k in ('weight','center','scale')}) for f in FEATURES}
        service.configure(session.get('vault'),cfg,int(form.get('config_id','')),restore)
        return redirect(url_for('economic_cycle'))
