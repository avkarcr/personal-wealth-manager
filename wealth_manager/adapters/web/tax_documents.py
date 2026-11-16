import csv
from io import BytesIO,StringIO
from uuid import uuid4
from flask import Request,request,render_template,redirect,url_for,send_file,Response
from wealth_manager.modules.taxes.documents import Evidence, TaxPayment, MAX_FILE_BYTES
from wealth_manager.shared.records import RecordError
from .commands import validated
from .forms import f_str,f_decimal

class TaxUploadRequest(Request):
    """Bound multipart bodies and keep sensitive uploads out of disk spool files."""
    @property
    def max_content_length(self):
        return 6*1024*1024 if self.path=='/taxes/documents/upload' else super().max_content_length
    def _get_file_stream(self,*args,**kwargs):
        if self.path=='/taxes/documents/upload':return BytesIO()
        return super()._get_file_stream(*args,**kwargs)


def register_tax_documents(app,runtime):
    app.request_class=TaxUploadRequest
    commands=runtime.services['taxes']
    @app.context_processor
    def tax_tokens():return dict(tax_request_id=uuid4().hex)
    @app.post('/taxes/payments')
    @validated
    def tax_payment_save():
        commands.pay(TaxPayment(entry_id=int(f_str('entry_id')),date=f_str('date'),amount=f_decimal('amount'),
            kind=f_str('kind'),note=f_str('note'),request_id=f_str('request_id')))
        return redirect(url_for('taxes'))
    @app.post('/taxes/payments/<int:pid>/cancel')
    @validated
    def tax_payment_cancel(pid):
        commands.cancel_payment(pid);return redirect(url_for('taxes'))
    @app.post('/taxes/documents/upload')
    @validated
    def tax_document_upload():
        file=request.files.get('file')
        if file is None:raise RecordError('Выберите документ')
        commands.attach(Evidence(filename=file.filename or '',content=file.read(MAX_FILE_BYTES+1),
            entry_id=int(f_str('entry_id')) if f_str('entry_id') else None,
            deduction_id=int(f_str('deduction_id')) if f_str('deduction_id') else None,
            date=f_str('date'),number=f_str('number'),issuer=f_str('issuer'),note=f_str('note')))
        return redirect(url_for('taxes'))
    @app.get('/taxes/documents/<int:did>/download')
    @validated
    def tax_document_download(did):
        doc=commands.document(did)
        response=send_file(BytesIO(doc['content']),mimetype='application/octet-stream',as_attachment=True,download_name=doc['filename'],max_age=0)
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
        return response
    @app.post('/taxes/documents/<int:did>/delete')
    @validated
    def tax_document_delete(did):
        commands.delete_document(did);return redirect(url_for('taxes'))
    @app.get('/taxes/export.csv')
    @validated
    def tax_export():
        report=runtime.services['tax_reports'].overview(request.args.get('year') or None,request.args.get('country') or None)
        out=StringIO();fields=('year','country','currency','base','tax','paid','balance','refund_expected','refund_received')
        writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader();writer.writerows(report['summary'])
        return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="tax-summary.csv"','Cache-Control':'no-store'})
