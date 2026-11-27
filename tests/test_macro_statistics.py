"""M4: statistical semantics, offline file mapping and signed atomic previews."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile,ZIP_DEFLATED
import re
import json
import base64
import unittest
import openpyxl
from vault_fixtures import ResearchCase
from wealth_manager.modules.macroeconomics.statistics import StatisticsImport,Upload,period,PROFILES
from wealth_manager.adapters.macroeconomics.files import FileReader
from wealth_manager.adapters.macroeconomics.session import Session
from wealth_manager.shared.records import RecordError
from wealth_manager.factory import create_app


def config(**changes):
    result=dict(indicator='cpi_mom_index',frequency='monthly',methodology='test-method-2024',source_url='https://rosstat.gov.ru/statistics/price',scale='1',base_year='',published_at='',publication_confirmed='',status='unknown',sheet='CSV',layout='rows',first_row='2',last_row='3',period_col='1',value_col='2',year_col='0',year_row='1',last_value_col='2')
    result.update(changes);return result

def csvfile(text='period;value\n2024-01;100,50\n2024-02;101,25\n',name='cpi_mom_index.csv'):return Upload(name,text.encode())

def xlsx(rows,sheet='Data'):
    book=openpyxl.Workbook();ws=book.active;ws.title=sheet
    for row in rows:ws.append(row)
    out=BytesIO();book.save(out);book.close();return Upload('source.xlsx',out.getvalue())

class FileTests(unittest.TestCase):
    def test_csv_encodings_decimal_precision_and_detection(self):
        f=csvfile('period;value\n2024-01;100,1234567890123456789\n2024-02;—\n')
        r=FileReader();self.assertEqual(r.inspect(f)['suggestion'],'cpi_mom_index')
        values,missing=r.extract(f,config(),'monthly');self.assertEqual(values[0][2],D('100.1234567890123456789'));self.assertEqual(missing,1)
        cp=Upload('source.csv','месяц;значение\n2024-01;101\n2024-02;102\n'.encode('cp1251'))
        self.assertEqual(len(r.extract(cp,config(),'monthly')[0]),2)
        self.assertEqual(r.inspect(csvfile(name='gdp.xlsx.csv'))['suggestion'],'')

    def test_xlsx_rows_and_year_matrix(self):
        f=xlsx([['месяц',2023,2024],['январь',100.1,100.2],['февраль',None,100.3]])
        r=FileReader();self.assertEqual(r.inspect(f)['sheets'][0]['rows'],3)
        rows,skipped=r.extract(f,config(sheet='Data',layout='matrix',last_value_col='3'),'monthly')
        self.assertEqual(rows[0],('2023-01-01','2023-01-31',D('100.1')));self.assertEqual(len(rows),3);self.assertEqual(skipped,1)
        q=xlsx([['quarter',2024],['I',102],['II квартал',103]])
        points,_=r.extract(q,config(sheet='Data',layout='matrix'),'quarterly')
        self.assertEqual(points[1][:2],('2024-04-01','2024-06-30'))
        dates=xlsx([['period','value'],[date(2024,1,1),101],[date(2024,2,1),102]])
        self.assertEqual(len(r.extract(dates,config(sheet='Data'),'monthly')[0]),2)

    def test_separate_year_column_and_strict_periods(self):
        f=csvfile('month;value;year\nянварь;100;2024\nфевраль;101;2024\n')
        rows,_=FileReader().extract(f,config(year_col='3'),'monthly');self.assertEqual(rows[-1][1],'2024-02-29')
        self.assertEqual(period('2024','annual'),('2024-01-01','2024-12-31'))
        self.assertEqual(period('IV квартал','quarterly',2024),('2024-10-01','2024-12-31'))
        for label,freq in [('2024-Q5','quarterly'),('2024-13','monthly'),('январь-июнь','monthly'),('2024-01-31','monthly')]:
            with self.assertRaises(ValueError):period(label,freq)

    def test_formulas_malformed_zip_and_resource_limits(self):
        r=FileReader()
        with self.assertRaises(RecordError):r.extract(xlsx([['p','v'],['2024-01','=1+1'],['2024-02',100]]),config(sheet='Data'),'monthly')
        for f in [Upload('old.xls',b'abc'),Upload('bad.xlsx',b'not a zip'),Upload('bad.csv',b'p;v\n"unterminated')]:
            with self.assertRaises(RecordError):r.tables(f)
        out=BytesIO()
        with ZipFile(out,'w',ZIP_DEFLATED) as z:z.writestr('large.xml',b'x'*8_000_001)
        with self.assertRaises(RecordError):r.tables(Upload('bomb.xlsx',out.getvalue()))
        for path in ('../evil','xl/vbaProject.bin','xl/externalLinks/link.xml'):
            out=BytesIO()
            with ZipFile(out,'w') as z:z.writestr(path,b'x')
            with self.assertRaises(RecordError):r.tables(Upload('bad.xlsx',out.getvalue()))
        for settings in [config(first_row='0'),config(last_row='99'),config(period_col='0'),config(layout='bad'),config(sheet='missing')]:
            with self.assertRaises(RecordError):r.extract(csvfile(),settings,'monthly')

class ImportTests(ResearchCase):
    def service(self):return StatisticsImport(FileReader(),Session(self.vault),today=lambda:date(2026,11,21))
    def preview(self,files=None,configs=None):return self.service().preview(files or [csvfile()],configs or [config()],self.vault.token)
    def confirm(self,files=None,configs=None):
        files=files or [csvfile()];configs=configs or [config()];p=self.preview(files,configs)
        return self.service().confirm(files,configs,self.vault.token,p['revision'])

    def test_preview_is_read_only_and_confirm_durable_idempotent(self):
        before=self.dump();disk=Path(self.vault.path).read_bytes();preview=self.preview()
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        self.assertEqual(preview['files'][0]['points'][0].value,D('100.5')) # not silently 0.5%
        self.assertEqual(self.confirm(),2);self.assertEqual(self.confirm(),2)
        self.assertEqual(self.vault.con.execute('SELECT COUNT(*) FROM macro_revisions').fetchone()[0],2)
        evidence=json.loads(self.vault.con.execute('SELECT content FROM macro_artifacts').fetchone()[0])
        self.assertEqual(base64.b64decode(evidence['content_base64']),csvfile().raw)
        self.assertEqual(evidence['mapping'],config())
        self.assertEqual(self.s['macro_data_reports'].overview('2024-12-31')['rows'],[])
        self.vault.lock();self.vault.unlock('password123');self.assertEqual(self.vault.con.execute('SELECT COUNT(*) FROM macro_revisions').fetchone()[0],2)

    def test_distinct_bases_units_and_seasonal_series(self):
        cpi=csvfile();gdp=csvfile('period;value\n2024-Q1;1000\n2024-Q2;1100\n','gdp.csv')
        for cfg in [config(indicator='gdp_real_level',frequency='quarterly',base_year='2021',scale='1000000000'),config(indicator='gdp_real_level',frequency='quarterly',base_year='2016',scale='1000000000'),config(indicator='gdp_nominal_level',frequency='quarterly'),config(indicator='gdp_real_qoq_sa_growth',frequency='quarterly')]:self.confirm([gdp],[cfg])
        self.confirm([cpi],[config()]);self.confirm([cpi],[config(indicator='cpi_yoy_index')])
        self.assertEqual(self.vault.con.execute('SELECT COUNT(*) FROM macro_series').fetchone()[0],6)
        rows=self.s['macro_data_reports'].overview('2024-12-31','latest')['rows'];real=next(r for r in rows if r['indicator']=='gdp_real_level')
        self.assertEqual(real['scale'],D(1000000000));self.assertEqual(real['unit'],'RUB')

    def test_publication_confirmation_and_whole_period_rules(self):
        for c in [config(published_at='2024-03-01'),config(published_at='2027-01-01',publication_confirmed='yes'),config(published_at='2024-01-01',publication_confirmed='yes'),config(indicator='gdp_real_level',frequency='monthly',base_year='2021'),config(base_year='2021'),config(scale='1000'),config(source_url='file:///etc/passwd'),config(methodology='')]:
            with self.assertRaises(RecordError):self.preview(configs=[c])
        self.confirm(configs=[config(published_at='2024-03-01',publication_confirmed='yes')])
        self.assertEqual(len(self.s['macro_data_reports'].overview('2024-03-01')['rows']),2)
        self.assertEqual(self.s['macro_data_reports'].overview('2024-02-29')['rows'],[])
        with self.assertRaises(RecordError):self.preview([csvfile('period;value\n2026-11;100\n2026-12;101\n')])

    def test_multi_file_batch_overlap_and_invalid_second_file_are_atomic(self):
        first=csvfile();second=csvfile(name='growth.csv');configs=[config(),config(indicator='cpi_yoy_index')]
        self.assertEqual(self.confirm([first,second],configs),4)
        before=self.dump()
        with self.assertRaises(RecordError):self.preview([first,first],[config(),config()])
        bad=csvfile('period;value\n2024-01;NaN\n2024-02;100\n')
        with self.assertRaises(ValueError):self.preview([first,bad],configs)
        self.assertEqual(self.dump(),before)
        with self.assertRaises(RecordError):self.service().inspect([Upload('big.csv',b'x'*524289)],self.vault.token)
        with self.assertRaises(RecordError):self.service().inspect([first]*6,self.vault.token)

    def test_stale_preview_lock_and_disk_failure(self):
        p=self.preview();self.s['macroeconomics'].save(self.observation)
        with self.assertRaises(RecordError):self.service().confirm([csvfile()],[config()],self.vault.token,p['revision'])
        before=self.dump();disk=Path(self.vault.path).read_bytes();p=self.preview()
        with patch.object(self.vault,'save',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.service().confirm([csvfile()],[config()],self.vault.token,p['revision'])
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        token=self.vault.token;self.vault.lock();self.vault.unlock('password123')
        with self.assertRaises(RecordError):self.service().confirm([csvfile()],[config()],token,p['revision'])

    def test_http_inspect_map_preview_confirm_localization_and_tampering(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as s:s['vault']=self.vault.token
        for lang,title in [('ru','Импорт статистики'),('en','Statistical data import')]:
            self.s['change_language'].execute(lang);self.assertIn(title,client.get('/macroeconomics/import').get_data(as_text=True))
        before=self.dump()
        response=client.post('/macroeconomics/import/inspect',data={'files':(BytesIO(csvfile().raw),'cpi_mom_index.csv')})
        self.assertEqual(response.status_code,200)
        token=re.search(r'name="token" value="([^"]+)"',response.get_data(as_text=True))[1]
        fields={'0_'+k:v for k,v in config().items()};fields['token']=token
        response=client.post('/macroeconomics/import/preview',data=fields);self.assertEqual(response.status_code,200)
        self.assertIn('Nothing has been saved',response.get_data(as_text=True));self.assertEqual(self.dump(),before)
        token2=re.search(r'name="token" value="([^"]+)"',response.get_data(as_text=True))[1]
        for payload in [dict(token=token2),dict(token=token,confirm='yes'),dict(token=token2+'bad',confirm='yes')]:
            self.assertEqual(client.post('/macroeconomics/import/confirm',data=payload).status_code,400)
        response=client.post('/macroeconomics/import/confirm',data=dict(token=token2,confirm='yes'));self.assertEqual(response.status_code,302)
        self.assertEqual(client.post('/macroeconomics/import/confirm',data=dict(token=token2,confirm='yes')).status_code,400)
        for path in ('inspect','preview','confirm'):self.assertEqual(client.get('/macroeconomics/import/'+path).status_code,405)
        self.assertEqual(client.get('/macroeconomics/import/template/cpi_mom_index').data,b'period;value\n')
        self.assertEqual(client.get('/macroeconomics/import/template/bad').status_code,404)

    def test_ui_escaping_upload_limits_and_expired_token(self):
        app=create_app({'TESTING':True},vault=self.vault);client=app.test_client()
        with client.session_transaction() as s:s['vault']=self.vault.token
        body=b'p;v\n<script>alert(1)</script>;100\n'
        response=client.post('/macroeconomics/import/inspect',data={'files':(BytesIO(body),'file.csv')})
        page=response.get_data(as_text=True);self.assertIn('&lt;script&gt;',page);self.assertNotIn('<script>alert(1)</script>',page)
        token=re.search(r'name="token" value="([^"]+)"',page)[1]
        from itsdangerous import SignatureExpired
        from itsdangerous import URLSafeTimedSerializer
        original=URLSafeTimedSerializer.loads
        def expire(obj,*args,**kwargs):
            if kwargs.get('max_age')==1200:raise SignatureExpired('expired')
            return original(obj,*args,**kwargs)
        with patch('wealth_manager.adapters.web.macro_import.URLSafeTimedSerializer.loads',new=expire):
            self.assertEqual(client.post('/macroeconomics/import/preview',data=dict(token=token)).status_code,400)
        for size, status in ((524289, 400), (1500001, 413)):
            upload = {'files': (BytesIO(b'x' * size), 'large.csv')}
            with client.post('/macroeconomics/import/inspect', data=upload) as response, response.request.input_stream:
                self.assertEqual(response.status_code, status)
        self.assertEqual(client.post('/macroeconomics/import/inspect',data={}).status_code,400)

    def test_empty_negative_invalid_series_and_real_gdp_require_base_year(self):
        for f,c in [(csvfile('period;value\n2024-01;\n2024-02;—\n'),config()),(csvfile('period;value\n2024-01;0\n2024-02;-1\n'),config()),(csvfile(),config(indicator='bad')),(csvfile('period;value\n2024-Q1;100\n2024-Q2;101\n'),config(indicator='gdp_real_level_sa',frequency='quarterly'))]:
            with self.assertRaises(RecordError):self.preview([f],[c])
        f=csvfile('period;value\n2024-Q1;100\n2024-Q2;101\n')
        self.confirm([f],[config(indicator='gdp_real_level_sa',frequency='quarterly',base_year='2021')])
        self.assertEqual(self.vault.con.execute('SELECT adjustment FROM macro_series').fetchone()[0],'sa')

    def test_mapping_publication_and_matrix_failures(self):
        r=FileReader();f=xlsx([['quarter',2024,2024],['I',1,2],['II',2,3]])
        for c in [config(sheet='Data',layout='matrix',last_value_col='3'),config(sheet='Data',layout='matrix',year_row='3'),config(sheet='Data',year_col='129')]:
            with self.assertRaises(RecordError):r.extract(f,c,'quarterly')
        with self.assertRaises(RecordError):self.service().bundles([csvfile()],[])
        self.assertEqual(self.service().inspect([csvfile()],self.vault.token)[0]['sheets'][0]['name'],'CSV')
        c=config(indicator='unemployment_rate')
        with self.assertRaises(RecordError):self.preview([csvfile('period;value\n2024-01;101\n2024-02;102\n')],[c])
