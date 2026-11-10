"""HTTP contract, localization and transactional behavior of command adapters."""
import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from db import init_db
from wealth_manager.factory import create_app
from wealth_manager.adapters.web.i18n import EN

class CommandRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.app=create_app({'TESTING':True,'VAULT_PATH':str(Path(self.tmp.name)/'data.enc')})
        self.runtime=self.app.extensions['wealth_manager'];self.addCleanup(self.runtime.close)
        self.vault=self.runtime.vault;init_db(self.vault.create('password123'));self.vault.save(force=True)
        self.client=self.app.test_client()
        with self.client.session_transaction() as session:session['vault']=self.vault.token
        self.post('/accounts/save',name='Home',kind='realty')
        self.aid=self.rows('accounts')[0]['id']

    def rows(self,table):return [dict(r) for r in self.vault.con.execute('SELECT * FROM '+table)]
    def post(self,url,**data):
        r=self.client.post(url,data=data); self.assertEqual(r.status_code,302,(url,r.get_data(as_text=True)));return r

    def test_account_position_history_forms(self):
        self.post(f'/accounts/{self.aid}/value',current_value='1 000,50')
        self.assertEqual(self.rows('accounts')[0]['current_value'],1000.5)
        self.post(f'/accounts/{self.aid}/archive',next='/settings')
        self.assertEqual(self.rows('accounts')[0]['archived'],1)
        self.post(f'/accounts/{self.aid}/history',date='2020-01-01',value='bad')
        self.post('/accounts/save',name='Broker',kind='broker')
        broker=self.rows('accounts')[-1]['id']
        self.post('/holdings/save',account_id=broker,ticker='sber',quantity='2',price='100')
        hid=self.rows('holdings')[0]['id']
        self.post('/holdings/save',id=hid,account_id=broker,name='Stock',quantity=2,price=110)
        self.post(f'/accounts/{broker}/prices',**{f'price_{hid}':'120,50','price_999':'bad','next':'/settings'})
        self.assertEqual(self.rows('holdings')[0]['price'],120.5)
        self.post(f'/holdings/{hid}/delete')
        self.post(f'/accounts/{self.aid}/delete')
        self.assertEqual(len(self.rows('accounts')),1)

    def test_flow_forms(self):
        self.post('/flows/save',account_id=self.aid,type='income',amount='-100',pending='on')
        fid=self.rows('cashflows')[0]['id']
        self.assertEqual((self.rows('cashflows')[0]['amount'],self.rows('cashflows')[0]['pending']),(100,1))
        self.post(f'/flows/{fid}/paid');self.assertFalse(self.rows('cashflows')[0]['pending'])
        self.post('/flows/save',account_id=self.aid,id=fid,type='expense',amount=50)
        self.post(f'/flows/{fid}/delete');self.assertEqual(self.rows('cashflows'),[])

    def test_rental_forms_and_protected_income(self):
        self.post('/leases/save',account_id=self.aid,tenant='Tenant',start_date='2024-01-01',end_date='2024-01-31',rent_amount=100,pay_day='oops',schedule='on')
        lid=self.rows('leases')[0]['id'];pid=self.rows('rent_payments')[0]['id']
        self.post('/leases/save',id=lid,account_id=self.aid,tenant='Tenant',start_date='2024-01-01',end_date='2024-01-31',rent_amount=100,pay_day=1)
        self.post(f'/leases/{lid}/schedule')
        self.post('/rent/save',id=pid,lease_id=lid,period_start='2024-01-01',status='received')
        fid=self.rows('cashflows')[0]['id']
        before=self.rows('cashflows')
        self.post('/flows/save',id=fid,account_id=self.aid,amount=999)
        self.post(f'/flows/{fid}/delete')
        r=self.post(f'/flows/{fid}/paid');self.assertTrue(r.location.endswith('#rent'))
        self.assertEqual(self.rows('cashflows'),before)
        self.post(f'/rent/{pid}/delete')
        self.post(f'/leases/{lid}/schedule')
        self.post(f'/leases/{lid}/delete');self.assertEqual(self.rows('leases'),[])

    def test_missing_and_invalid_forms_are_rejected(self):
        urls=['/accounts/999/value','/accounts/999/archive','/accounts/999/delete','/accounts/999/history','/holdings/999/delete','/flows/999/paid','/flows/999/delete','/leases/999/schedule','/leases/999/delete','/rent/999/delete']
        for url in urls:
            with self.subTest(url=url):self.assertEqual(self.client.post(url).status_code,404)
        for url,data in [('/holdings/save',{'account_id':self.aid,'quantity':'NaN'}),('/flows/save',{'account_id':self.aid,'type':'bad'}),('/leases/save',{'account_id':self.aid,'frequency':'bad'}),('/rent/save',{'lease_id':'bad'}),('/accounts/save',{'current_value':'not money'}),(f'/accounts/{self.aid}/prices',{'price_1':'Infinity'})]:
            with self.subTest(url=url):self.assertEqual(self.client.post(url,data=data).status_code,400)

    def test_http_failure_cannot_be_saved_by_after_request(self):
        before=list(self.vault.con.iterdump())
        with patch('vault.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.client.post('/flows/save',data={'account_id':self.aid,'amount':100})
        self.assertEqual(list(self.vault.con.iterdump()),before)
        self.assertFalse(self.vault.dirty())
        self.assertEqual(self.client.get('/settings').status_code,200)
        self.assertEqual(self.rows('cashflows'),[])

    def test_commands_have_no_persistence_access(self):
        tree=ast.parse(Path('wealth_manager/adapters/web/commands.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute):self.assertNotIn(node.attr,('connection','con','get_connection','commit','take_snapshot'))

    def test_command_messages_are_localized_and_user_input_preserved(self):
        self.post('/settings/general',language='en')
        self.post('/leases/save',account_id=self.aid,tenant='Арендатор Иван',start_date='2024-01-01',end_date='2024-01-31',schedule='on')
        page=self.client.get(f'/accounts/{self.aid}').get_data(as_text=True)
        self.assertIn('Lease saved',page)
        self.assertIn('Арендатор Иван',page)
        tree=ast.parse(Path('wealth_manager/adapters/web/commands.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('flash','gettext') and node.args and isinstance(node.args[0],ast.Constant):
                message=node.args[0].value
                if any('А'<=c<='я' for c in message):self.assertIn(message,EN)
