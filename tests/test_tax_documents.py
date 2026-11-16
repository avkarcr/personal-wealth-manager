"""Tax evidence encryption, independent refunds and auditable payments."""
from dataclasses import replace,asdict
from datetime import date,timedelta
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import db
import test_research
from wealth_manager.factory import create_app
from wealth_manager.modules.taxes.documents import Evidence,TaxPayment,MAX_FILE_BYTES,MAX_TOTAL_BYTES
from wealth_manager.modules.taxes.domain import TaxEntry,Deduction
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound

PDF=b'%PDF-1.4\nPRIVATE EVIDENCE FOR TEST\n%%EOF'
PNG=b'\x89PNG\r\n\x1a\nTEST'
JPEG=b'\xff\xd8\xffTEST'

class TaxEvidenceTests(unittest.TestCase):
    base_setup=test_research.ResearchTests.setUp
    dump=test_research.ResearchTests.dump
    def setUp(self):
        self.base_setup();self.cmd=self.s['taxes'];self.day=date.today().isoformat()
        self.eid=self.cmd.save_entry(self.entry);self.did=self.cmd.save_deduction(self.deduction)
        self.app=create_app({'TESTING':True},vault=self.vault);self.client=self.app.test_client()
        with self.client.session_transaction() as session:session['vault']=self.vault.token
    def payment(self,**kw):
        data=dict(entry_id=self.eid,date=self.day,amount=D('100.01'),request_id='payment')
        data.update(kw);return TaxPayment(**data)
    def evidence(self,**kw):
        data=dict(filename='Справка.pdf',content=PDF,deduction_id=self.did,date=self.day,number='123',issuer='Issuer')
        data.update(kw);return Evidence(**data)

    def test_payment_totals_baseline_idempotency_cancel_and_no_cash_mutation(self):
        before=self.s['reports'].dashboard()
        p=self.payment();pid=self.cmd.pay(p);self.assertEqual(self.cmd.pay(p),pid)
        self.cmd.pay(self.payment(amount=D('0.02'),kind='withheld',request_id='withheld'))
        report=self.s['tax_reports'].overview();e=report['entries'][0]
        self.assertEqual(e['paid'],5000);self.assertEqual(e['paid_total'],D('5100.03'))
        self.assertEqual(report['summary'][0]['balance'],D('7899.97'))
        self.assertEqual(self.s['reports'].dashboard(),before)
        with self.assertRaises(RecordError):self.cmd.pay(replace(p,amount=D(2)))
        self.cmd.cancel_payment(pid);self.cmd.cancel_payment(pid)
        self.assertEqual(self.s['tax_reports'].overview()['summary'][0]['paid'],D('5000.02'))
        with self.assertRaises(RecordError):self.cmd.pay(p)
        with self.assertRaises(RecordError):self.cmd.delete_entry(self.eid)
        with self.assertRaises(RecordError):self.cmd.save_entry(replace(self.entry,id=self.eid,year=2025))
        self.cmd.save_entry(replace(self.entry,id=self.eid,base=D('200000')))
        self.assertEqual(self.s['tax_reports'].overview()['summary'][0]['tax'],26000)

    def test_partial_refund_does_not_erase_actual_receipt_or_reduce_tax(self):
        record=replace(self.deduction,id=self.did,status='submitted',refund=D(1300),actual_refund=D(400),received_date=self.day)
        self.cmd.save_deduction(record)
        s=self.s['tax_reports'].overview()['summary'][0]
        self.assertEqual((s['refund_expected'],s['refund_received'],s['balance']),(900,400,8000))
        self.cmd.save_deduction(replace(record,status='rejected'))
        s=self.s['tax_reports'].overview()['summary'][0]
        self.assertEqual((s['refund_expected'],s['refund_received']),(0,400))
        self.cmd.save_deduction(replace(record,status='received',actual_refund=D(1400)))
        s=self.s['tax_reports'].overview()['summary'][0]
        self.assertEqual((s['refund_expected'],s['refund_received']),(0,1400))
        with self.assertRaises(RecordError):self.cmd.save_deduction(replace(record,received_date=(date.today()+timedelta(days=1)).isoformat()))
        with self.assertRaises(RecordError):self.cmd.save_deduction(replace(record,actual_refund=D(-1)))

    def test_attachments_are_deduplicated_encrypted_and_reopen_as_identical_bytes(self):
        evidence=self.evidence();did=self.cmd.attach(evidence)
        self.assertEqual(self.cmd.attach(replace(evidence,filename='renamed.pdf')),did)
        report=self.s['tax_reports'].overview();docs=report['deductions'][0]['attachments']
        self.assertEqual(len(docs),1);self.assertNotIn('content',docs[0]);self.assertEqual(docs[0]['number'],'123')
        self.assertNotIn(PDF,Path(self.vault.path).read_bytes())
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.cmd.document(did)['content'],PDF)
        # The Python 3.9 SQL-dump path must preserve binary attachments too.
        connection=self.vault.con
        class DumpOnly:
            def __getattr__(self,name):
                if name=='serialize':raise AttributeError(name)
                return getattr(connection,name)
        self.vault.con=DumpOnly();self.vault.save(force=True);self.vault.con=connection
        self.vault.lock();self.vault.unlock('password123');db.init_db(self.vault.con)
        self.assertEqual(self.cmd.document(did)['content'],PDF)
        self.assertEqual(self.vault.con.execute('PRAGMA user_version').fetchone()[0],13)

    def test_attachment_targets_metadata_identity_and_delete_guards(self):
        did=self.cmd.attach(self.evidence())
        with self.assertRaises(RecordError):self.cmd.delete_deduction(self.did)
        with self.assertRaises(RecordError):self.cmd.save_deduction(replace(self.deduction,id=self.did,country='AE'))
        self.cmd.save_deduction(replace(self.deduction,id=self.did,note='Updated'))
        eid=self.cmd.attach(self.evidence(entry_id=self.eid,deduction_id=None))
        self.assertNotEqual(did,eid)
        with self.assertRaises(RecordError):self.cmd.delete_entry(self.eid)
        with self.assertRaises(RecordError):self.cmd.save_entry(replace(self.entry,id=self.eid,currency='USD'))
        self.cmd.delete_document(did);self.cmd.delete_deduction(self.did)
        self.cmd.delete_document(eid);self.cmd.delete_entry(self.eid)
        with self.assertRaises(EntityNotFound):self.cmd.document(did)
        with self.assertRaises(EntityNotFound):self.cmd.delete_document(did)
        with self.assertRaises(EntityNotFound):self.cmd.attach(self.evidence())

    def test_input_limits_formats_filename_safety_and_total_quota(self):
        for patch_data in (dict(content=b''),dict(content=b'x'*(MAX_FILE_BYTES+1)),dict(content=b'<html>'),dict(filename='bad.html'),dict(filename='bad\x00.pdf'),dict(filename=''),dict(entry_id=self.eid),dict(deduction_id=None),dict(date=(date.today()+timedelta(days=1)).isoformat()),dict(number='n'*201)):
            with self.subTest(data=str(patch_data)[:90]),self.assertRaises(RecordError):self.cmd.attach(self.evidence(**patch_data))
        for filename,content in (('../photo.png',PNG),('C:\\temp\\photo.jpg',JPEG)):
            did=self.cmd.attach(self.evidence(filename=filename,content=content))
            self.assertEqual(self.cmd.document(did)['filename'],filename.replace('\\','/').split('/')[-1])
        from wealth_manager.adapters.persistence.tax_evidence import TaxEvidenceRepository
        with patch.object(TaxEvidenceRepository,'size',return_value=MAX_TOTAL_BYTES):
            with self.assertRaises(RecordError):self.cmd.attach(self.evidence())
        for kw in (dict(amount=D(0)),dict(amount=D(-1)),dict(amount=D('1e19')),dict(kind='bad'),dict(date='bad'),dict(date=(date.today()+timedelta(days=1)).isoformat()),dict(request_id='')):
            with self.subTest(kw=kw),self.assertRaises((ValueError,RecordError)):self.cmd.pay(self.payment(**kw))
        with self.assertRaises(EntityNotFound):self.cmd.pay(self.payment(entry_id=999))

    def test_file_and_payment_disk_failure_roll_back_memory_and_ciphertext(self):
        for action in (lambda:self.cmd.attach(self.evidence()),lambda:self.cmd.pay(self.payment())):
            before=self.dump();disk=Path(self.vault.path).read_bytes()
            with patch('vault.os.replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):action()
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        did=self.cmd.attach(self.evidence());pid=self.cmd.pay(self.payment())
        for action in (lambda:self.cmd.delete_document(did),lambda:self.cmd.cancel_payment(pid)):
            before=self.dump()
            with patch('vault.os.replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):action()
            self.assertEqual(self.dump(),before)

    def test_filters_keep_countries_currencies_and_tax_years_separate(self):
        other=self.cmd.save_entry(replace(self.entry,year=2025,country='AE',currency='AED'))
        self.cmd.pay(self.payment(entry_id=other))
        self.cmd.attach(self.evidence(entry_id=other,deduction_id=None))
        selected=self.s['tax_reports'].overview('2025','ae')
        self.assertEqual(len(selected['entries']),1);self.assertEqual(selected['deductions'],[])
        self.assertEqual(selected['summary'][0]['currency'],'AED');self.assertEqual(len(selected['entries'][0]['attachments']),1)
        self.assertEqual(selected['years'],[2026,2025])
        self.assertEqual(self.s['tax_reports'].overview(2025,'RU')['summary'],[])
        for url in ('/taxes?year=abc','/taxes?country=XYZ','/taxes/export.csv?year=0'):
            self.assertEqual(self.client.get(url).status_code,400)
        export=self.client.get('/taxes/export.csv?year=2025&country=AE')
        self.assertEqual(export.status_code,200);self.assertIn(b'2025,AE,AED',export.data)
        self.assertNotIn(b'2026,RU',export.data);self.assertEqual(export.headers['Cache-Control'],'no-store')

    def test_http_upload_download_payment_and_refund_locales_and_no_read_writes(self):
        data={'deduction_id':self.did,'file':(BytesIO(PDF),'receipt.pdf'),'issuer':'<script>x</script>'}
        self.assertEqual(self.client.post('/taxes/documents/upload',data=data).status_code,302)
        did=self.s['tax_reports'].overview()['deductions'][0]['attachments'][0]['id']
        payment=asdict(self.payment())
        for _ in range(2):self.assertEqual(self.client.post('/taxes/payments',data=payment).status_code,302)
        self.assertEqual(self.s['tax_reports'].overview()['entries'][0]['paid_total'],D('5100.01'))
        refund=asdict(replace(self.deduction,id=self.did,status='submitted',actual_refund=D(400),received_date=self.day))
        self.assertEqual(self.client.post('/taxes/deductions/save',data=refund).status_code,302)
        for language,term in [('ru','Вложения'),('en','Attachments')]:
            self.s['change_language'].execute(language);before=self.dump();disk=Path(self.vault.path).read_bytes()
            response=self.client.get('/taxes');self.assertEqual(response.status_code,200)
            self.assertIn(term,response.get_data(as_text=True));self.assertNotIn('<script>x</script>',response.get_data(as_text=True))
            downloaded=self.client.get(f'/taxes/documents/{did}/download')
            self.assertEqual(downloaded.data,PDF);self.assertIn('attachment;',downloaded.headers['Content-Disposition'])
            self.assertEqual(downloaded.headers['Cache-Control'],'no-store');self.assertEqual(downloaded.headers['X-Content-Type-Options'],'nosniff')
            self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        pid=self.s['tax_reports'].overview()['entries'][0]['payments'][0]['id']
        self.assertEqual(self.client.post(f'/taxes/payments/{pid}/cancel').status_code,302)
        self.assertEqual(self.client.post(f'/taxes/documents/{did}/delete').status_code,302)
        self.assertEqual(self.client.get(f'/taxes/documents/{did}/download').status_code,404)

    def test_upload_limits_stay_in_memory_and_lock_blocks_document_access(self):
        from wealth_manager.adapters.web.tax_documents import TaxUploadRequest
        original=TaxUploadRequest._get_file_stream
        from werkzeug.test import encode_multipart
        from werkzeug.datastructures import FileStorage
        def upload_bytes(content):
            file=FileStorage(stream=BytesIO(content),filename='large.pdf')
            try:boundary,body=encode_multipart({'deduction_id':str(self.did),'file':file})
            finally:file.close()
            return self.client.post('/taxes/documents/upload',data=body,content_type='multipart/form-data; boundary='+boundary)
        streams=[]
        def capture(request,*args,**kwargs):
            stream=original(request,*args,**kwargs);streams.append(stream);return stream
        with patch.object(TaxUploadRequest,'_get_file_stream',capture):
            self.assertEqual(upload_bytes(b'%PDF-'+b'x'*600000).status_code,302)
        self.assertTrue(streams);self.assertTrue(all(isinstance(stream,BytesIO) for stream in streams))
        self.assertEqual(self.client.post('/taxes/documents/upload',data={}).status_code,400)
        self.assertEqual(upload_bytes(b'%PDF-'+b'x'*MAX_FILE_BYTES).status_code,400)
        self.assertEqual(self.client.post('/taxes/documents/upload',data=b'x'*(6*1024*1024+1),content_type='application/octet-stream').status_code,413)
        did=self.cmd.attach(self.evidence())
        self.vault.lock()
        self.assertEqual(self.client.get(f'/taxes/documents/{did}/download').status_code,302)

class TaxDocumentMigrationTests(unittest.TestCase):
    def test_v12_refunds_preserved_without_fabricated_receipt_dates(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        with patch('db._migrate_tax_documents'):db.init_db(c)
        for status,refund in [('received','123.45'),('submitted','200')]:
            c.execute("INSERT INTO tax_deductions(year,country,category,currency,amount,refund,status,documents) VALUES (2026,'RU','Education','RUB','1000',?,?, 'folder')",(refund,status))
        c.commit();before=list(c.iterdump());original=db._migrate_tax_documents
        def fail(con):original(con);raise RuntimeError('migration')
        with patch('db._migrate_tax_documents',side_effect=fail):
            with self.assertRaises(RuntimeError):db.init_db(c)
        self.assertEqual(list(c.iterdump()),before)
        db.init_db(c)
        rows=c.execute('SELECT actual_refund,received_date,documents FROM tax_deductions ORDER BY id').fetchall()
        self.assertEqual(tuple(rows[0]),(D('123.45'),'','folder'));self.assertEqual(rows[1][0],0)
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        before=list(c.iterdump());db.init_db(c);self.assertEqual(list(c.iterdump()),before)
