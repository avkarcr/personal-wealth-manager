"""Dashboard HTTP adapter; refresh reuses M5's guarded scheduler."""
from datetime import date,timedelta
from flask import request,render_template,redirect,url_for,session,g,flash
from wealth_manager.modules.macro_dashboard.application import RANGES,parameters,ERROR
from wealth_manager.modules.macro_dashboard.charts import CHARTS,UNITS
from wealth_manager.modules.economic_cycle.config import ROLE_LABELS,FEATURES
from wealth_manager.modules.economic_cycle.model import PHASES,DESCRIPTIONS,TRANSITIONS,REASONS
from wealth_manager.modules.economic_cycle.signals import REASONS as SIGNAL_REASONS
from wealth_manager.modules.macroeconomics.derived import MESSAGES
from wealth_manager.modules.macroeconomics.data import instant
from wealth_manager.modules.macroeconomics.refresh import RefreshError
from wealth_manager.modules.macroeconomics.scheduling import KINDS
from .commands import validated
from .i18n import gettext

SIGNALS=dict(off='Группа отключена',missing='Нет доступных сигналов',support='Поддерживает фазу',against='Противоречит фазе',mixed='Смешанный сигнал')
STATUSES=dict(success='Успешно',empty='Новых данных нет',failed='Ошибка загрузки',running='Загрузка начата; завершение не подтверждено',interrupted='Загрузка прервана')


def register_macro_dashboard(app,runtime):
    service=runtime.services['macro_dashboard']
    def params(values):
        return dict(as_of=values.get('as_of') or None,mode=values.get('mode') or 'known',window=values.get('window') or '5',chart=values.get('chart') or 'gdp',page=int(values.get('page') or 1))

    @app.get('/macroeconomics/dashboard')
    @validated
    def macro_dashboard():
        view=service.overview(session.get('vault'),**params(request.args))
        graph=view['graph']
        # JSON contains exact numeric strings; only SVG coordinates use JS numbers.
        chart_data=dict(graph,phase_labels={k:gettext(v) for k,v in PHASES.items()},unit_label=gettext(UNITS.get(graph['unit'],graph['unit'])))
        return render_template('macro_dashboard.html',**view,chart_data=chart_data,ranges=RANGES,charts=CHARTS,units=UNITS,phases=PHASES,
                               descriptions=DESCRIPTIONS,transitions=TRANSITIONS,reasons=REASONS,signal_reasons=dict(SIGNAL_REASONS,range='В выбранном интервале нет наблюдений'),
                               signals=SIGNALS,statuses=STATUSES,roles=ROLE_LABELS,features=FEATURES,messages=MESSAGES)

    @app.post('/macroeconomics/dashboard/refresh')
    @validated
    def macro_dashboard_refresh():
        values=params(request.form);parameters(values['window'],values['chart'],values['page'])
        if values['mode'] not in ('known','latest'):raise RefreshError(ERROR)
        if values['as_of'] and instant(values['as_of'],True)[:10]>instant(service.clock())[:10]:raise RefreshError(ERROR)
        kind=request.form.get('kind','')
        if kind not in KINDS:raise RefreshError('Некорректные настройки обновления')
        scheduler=runtime.services['macro_scheduler'];token=session.get('vault')
        end=date.fromisoformat(instant(scheduler.clock())[:10])-timedelta(days=1);start=end-timedelta(days=6)
        lock=g.wealth_vault_lock;lock.release()
        try:
            scheduler.run(kind,str(start),str(end),token)
            flash(gettext('Обновление завершено. Результат доступен в рядах данных и журнале'))
        except RefreshError as error:flash(gettext(str(error)))
        except Exception:
            app.logger.exception('Macro dashboard refresh failed')
            flash(gettext('Не удалось обновить данные. Последняя сохранённая версия доступна; проверьте журнал загрузок.'))
        finally:
            lock.acquire()
        if not runtime.vault.unlocked or runtime.vault.token!=token:return redirect(url_for('unlock'))
        return redirect(url_for('macro_dashboard',**values))
