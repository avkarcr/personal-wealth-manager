"""Three-step statistical import: inspect, map/preview, confirm signed immutable input."""
import base64
from pathlib import PurePosixPath
from flask import request,render_template,redirect,url_for,flash,Response,abort
from itsdangerous import URLSafeTimedSerializer,BadData
from wealth_manager.modules.macroeconomics.statistics import Upload,PROFILES,MAX_TOTAL,MAX_FILES
from wealth_manager.shared.records import RecordError
from wealth_manager.modules.macroeconomics.data import LABELS
from .commands import validated
from .i18n import gettext

FIELDS=('indicator','frequency','methodology','source_url','scale','base_year','published_at','publication_confirmed','status',
        'sheet','layout','first_row','last_row','period_col','value_col','year_col','year_row','last_value_col')

def register_macro_import(app,runtime):
    service=runtime.services['macro_import']
    signer=URLSafeTimedSerializer(app.config['SECRET_KEY'],salt='macro-statistics-v1')
    def page(**kwargs):return render_template('macro_import.html',profiles=PROFILES,labels=LABELS,**kwargs)
    def unpack(stage):
        try:
            payload=signer.loads(request.form.get('token',''),max_age=1200)
            if payload['vault']!=runtime.vault.token or payload['stage']!=stage:raise BadData('Context changed')
            files=[Upload(f['name'],base64.b64decode(f['raw'],validate=True)) for f in payload['files']]
            service.check_files(files)
            return payload,files
        except (BadData,KeyError,ValueError,TypeError) as error:
            raise RecordError('Предпросмотр истёк или недействителен. Загрузите файлы заново') from error

    @app.before_request
    def limit_macro_request():
        if request.endpoint in ('macro_inspect','macro_import_preview','macro_import_confirm') and (request.content_length or 0)>1_500_000:abort(413)

    @app.get('/macroeconomics/import')
    def macro_import():return page()

    @app.get('/macroeconomics/import/template/<indicator>')
    def macro_template(indicator):
        if indicator not in PROFILES:abort(404)
        return Response('period;value\n',mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename="{indicator}.csv"'})

    @app.post('/macroeconomics/import/inspect')
    @validated
    def macro_inspect():
        uploads=request.files.getlist('files')
        if not 1<=len(uploads)<=MAX_FILES:raise RecordError('До 5 файлов, суммарно не более 512 КиБ')
        files=[];remaining=MAX_TOTAL
        for f in uploads:
            name=PurePosixPath((f.filename or '').replace('\\','/')).name
            raw=f.read(remaining+1);remaining-=len(raw)
            if remaining<0:raise RecordError('До 5 файлов, суммарно не более 512 КиБ')
            files.append(Upload(name,raw))
        inspected=service.inspect(files,runtime.vault.token)
        token=signer.dumps(dict(stage='mapping',vault=runtime.vault.token,files=[dict(name=f.name,raw=base64.b64encode(f.raw).decode()) for f in files]))
        return page(inspected=inspected,token=token)

    @app.post('/macroeconomics/import/preview')
    @validated
    def macro_import_preview():
        payload,files=unpack('mapping')
        configs=[{name:request.form.get(f'{i}_{name}','').strip() for name in FIELDS} for i in range(len(files))]
        preview=service.preview(files,configs,runtime.vault.token)
        payload.update(stage='confirm',configs=configs,revision=preview['revision'])
        return page(preview=preview,token=signer.dumps(payload))

    @app.post('/macroeconomics/import/confirm')
    @validated
    def macro_import_confirm():
        payload,files=unpack('confirm')
        if request.form.get('confirm')!='yes':raise RecordError('Подтвердите проверку показателей, периодов и единиц')
        service.confirm(files,payload['configs'],runtime.vault.token,payload['revision'])
        flash(gettext('Статистические данные импортированы'))
        return redirect(url_for('macro_data'))
