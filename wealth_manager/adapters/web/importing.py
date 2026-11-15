import base64
from functools import wraps
from flask import request, render_template, redirect, url_for, flash, Response, abort
from itsdangerous import URLSafeTimedSerializer, BadData
from wealth_manager.shared.records import RecordError
from wealth_manager.adapters.importing.csv import MAX_BYTES, OPERATIONS, BALANCES
from wealth_manager.modules.ledger.domain import KINDS


def register_importing(app,runtime):
    service=runtime.services['imports']
    signer=URLSafeTimedSerializer(app.config['SECRET_KEY'],salt='statement-preview-v1')

    def checked(handler):
        @wraps(handler)
        def wrapped(*args,**kwargs):
            try:return handler(*args,**kwargs)
            except RecordError as error:
                return render_template('import_error.html',error=str(error),line=getattr(error,'line',None)),400
            except ValueError:
                return render_template('import_error.html',error='Проверьте счёт, дату и поля файла',line=None),400
        return wrapped

    @app.before_request
    def import_size_limit():
        if request.endpoint in ('import_preview','import_confirm','import_reconcile') and (request.content_length or 0)>3*MAX_BYTES:
            abort(413)

    def upload():
        file=request.files.get('file')
        if file is None:raise RecordError('Выберите CSV-файл')
        return file.read(MAX_BYTES+1)

    def page(**kwargs):
        return render_template('imports.html',ledger=runtime.services['ledger_reports'].overview(),kinds=KINDS,**kwargs)

    @app.get('/imports')
    def imports():return page()

    @app.get('/imports/template/<kind>')
    def import_template(kind):
        if kind not in ('operations','balances'):abort(404)
        return Response(','.join(OPERATIONS if kind=='operations' else BALANCES)+'\n',mimetype='text/csv',
                        headers={'Content-Disposition':f'attachment; filename="{kind}-v1.csv"'})

    @app.post('/imports/preview')
    @checked
    def import_preview():
        raw=upload();aid=int(request.form.get('account_id',''));source=request.form.get('source','').strip()
        preview=service.operations(raw,source,aid)
        token=signer.dumps(dict(raw=base64.b64encode(raw).decode(),aid=aid,source=source,
                               revision=preview['revision'],vault=runtime.vault.token))
        return page(preview=preview,token=token,source=source,account_id=aid)

    @app.post('/imports/confirm')
    @checked
    def import_confirm():
        try:
            payload=signer.loads(request.form.get('token',''),max_age=1800)
            if payload['vault']!=runtime.vault.token:raise BadData('vault changed')
        except BadData as error:
            raise RecordError('Предпросмотр истёк или недействителен. Загрузите файл заново') from error
        service.operations(base64.b64decode(payload['raw']),payload['source'],payload['aid'],expected_revision=payload['revision'])
        flash('Импорт завершён')
        return redirect(url_for('imports'))

    @app.post('/imports/reconcile')
    @checked
    def import_reconcile():
        raw=upload();aid=int(request.form.get('account_id',''));day=request.form.get('date','')
        result=service.balances(raw,aid,day)
        return page(comparison=result,account_id=aid,comparison_date=day)
