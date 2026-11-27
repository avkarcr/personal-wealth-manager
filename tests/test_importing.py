"""Stage 5: explicit previews, atomic imports, overlap and dated reconciliation."""
import csv
import html
import io
import re
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal as D
from vault_fixtures import LedgerCase
from wealth_manager.adapters.importing.csv import CsvStatements, OPERATIONS, BALANCES, MAX_BYTES
from wealth_manager.shared.records import RecordError
from wealth_manager.modules.importing.domain import ImportRowError
from wealth_manager.modules.positions.domain import Position


def csv_bytes(columns,rows,delimiter=','):
    stream=io.StringIO();writer=csv.writer(stream,delimiter=delimiter)
    writer.writerow(columns);writer.writerows(rows);return stream.getvalue().encode('utf-8-sig')


class CsvTests(unittest.TestCase):
    def test_utf8_bom_semicolon_quoted_notes_and_precision(self):
        raw=csv_bytes(OPERATIONS,[['x','2026-11-15','deposit',1,'RUB','0.1234567890123456789','','','','','Текст; "слово"\nстрока']],';')
        row=CsvStatements().operations(raw)[0]
        self.assertEqual(row.entry.quantity,D('0.1234567890123456789'))
        self.assertIn('\n',row.entry.note);self.assertEqual(row.line,3)
    def test_reject_malformed_unsupported_empty_and_oversized(self):
        parser = CsvStatements()
        row_limit = 500  # Public CSV v1 limit for both formats.
        trade = ['x', '2026-11-15', 'deposit', 1, 'RUB', '1', 0, 0, '', '', '']
        for parse, columns, valid_row in ((parser.operations, OPERATIONS, trade),
                                          (parser.balances, BALANCES, [1, 'RUB', 1])):
            header = (','.join(columns) + '\n').encode()
            with self.subTest(format=parse.__name__):
                self.assertEqual(len(parse(csv_bytes(columns, [valid_row] * row_limit))), row_limit)
                invalid = (b'', b'x' * (MAX_BYTES + 1), b'\xff', b'wrong\n1', header,
                           header + b'"unterminated', csv_bytes(columns, [valid_row[:-1]]),
                           csv_bytes(columns, [valid_row] * (row_limit + 1)))
                for raw in invalid:
                    with self.subTest(prefix=raw[:50]), self.assertRaises(RecordError):
                        parse(raw)
        for q in ('NaN','inf','1,23','1e999','bad'):
            with self.subTest(q=q),self.assertRaises(RecordError):parser.balances(csv_bytes(BALANCES,[[1,'RUB',q]]))
            with self.assertRaises(RecordError):parser.operations(csv_bytes(OPERATIONS,[['x','2026-11-15','deposit',1,'RUB',q,0,0,'','','']]))


class ImportTests(LedgerCase):

    def rows(self,operations):
        return csv_bytes(OPERATIONS,operations)
    def deposit(self,key='d',amount='100',day=None):
        return [key,day or self.day,'deposit',self.cash,'RUB',amount,0,0,'','','From statement']
    def buy(self,key='b',amount='2',day=None):
        return [key,day or self.day,'buy',self.hid,'RUB',amount,20,1,self.cash,'','Stock']
    def run_import(self,raw,source='broker'):
        preview=self.s['imports'].operations(raw,source,self.aid)
        return self.s['imports'].operations(raw,source,self.aid,expected_revision=preview['revision'])

    def test_preview_never_saves_and_commit_is_atomic_and_durable(self):
        self.prepare(0);raw=self.rows([self.deposit(),self.buy()])
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        preview=self.s['imports'].operations(raw,'broker',self.aid)
        self.assertEqual(preview['new'],2);self.assertEqual(self.dump(),before)
        self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        self.s['imports'].operations(raw,'broker',self.aid,expected_revision=preview['revision'])
        self.assertEqual(self.row(self.cash)['quantity'],59);self.assertEqual(self.row(self.hid)['quantity'],2)
        self.assertEqual(self.row(self.hid)['avg_price'],D('20.5'))
        self.vault.lock();self.vault.unlock('password123')
        self.assertEqual(self.row(self.cash)['quantity'],59)

    def test_overlap_idempotency_conflicts_and_cancelled_rows(self):
        self.prepare(0);self.run_import(self.rows([self.deposit()]))
        preview=self.s['imports'].operations(self.rows([self.deposit(),self.buy()]),'broker',self.aid)
        self.assertEqual([r['status'] for r in preview['rows']],['existing','new'])
        self.run_import(self.rows([self.deposit(),self.buy()]))
        self.assertEqual(self.s['imports'].operations(self.rows([self.deposit(),self.buy()]),'broker',self.aid)['new'],0)
        self.assertEqual(self.row(self.cash)['quantity'],59)
        with self.assertRaises(RecordError):self.run_import(self.rows([self.deposit(amount='200')]))
        eid=self.vault.con.execute("SELECT id FROM ledger_entries WHERE kind='buy'").fetchone()[0]
        self.cmd.cancel(eid)
        with self.assertRaises(RecordError):self.run_import(self.rows([self.buy()]))
        self.run_import(self.rows([self.deposit()]),source='second broker')
        self.assertEqual(self.row(self.cash)['quantity'],200)

    def test_failure_in_later_row_and_disk_failure_restore_everything(self):
        self.prepare(0)
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        with self.assertRaises(RecordError):self.run_import(self.rows([self.deposit(),self.buy(amount='99')]))
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        raw=self.rows([self.deposit(),self.buy()]);preview=self.s['imports'].operations(raw,'broker',self.aid)
        with patch('vault.os.replace',side_effect=OSError('full')):
            with self.assertRaises(OSError):self.s['imports'].operations(raw,'broker',self.aid,expected_revision=preview['revision'])
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)

        # Retry the same confirmation after restoring the durable vault.
        self.vault.lock()
        self.vault.unlock('password123')
        result = self.s['imports'].operations(raw, 'broker', self.aid, expected_revision=preview['revision'])
        self.assertEqual(result['new'], 2)
        self.assertEqual(self.run_import(raw)['new'], 0)
        self.assertEqual((self.row(self.cash)['quantity'], self.row(self.hid)['quantity']), (59, 2))

    def test_stale_preview_and_duplicate_external_id_rejected(self):
        self.prepare(0);raw=self.rows([self.deposit()]);preview=self.s['imports'].operations(raw,'broker',self.aid)
        self.s['positions'].update_prices(self.aid,{self.hid:D(21)})
        with self.assertRaises(RecordError):self.s['imports'].operations(raw,'broker',self.aid,expected_revision=preview['revision'])
        with self.assertRaises(RecordError):self.run_import(self.rows([self.deposit(),self.deposit()]))
        with self.assertRaises(RecordError):self.run_import(raw,source='')
        with self.assertRaises(RecordError):self.run_import(self.rows([self.deposit(key='')]))

    def test_wrong_account_currency_unknown_position_and_nonledger_account(self):
        cash_before_activation = self.s['positions'].save(Position(self.aid, name='Manual cash', asset_class_code='cash'))
        raw = self.rows([['d', self.day, 'deposit', cash_before_activation, 'RUB', 100, 0, 0, '', '', '']])
        before = self.dump()
        with self.assertRaisesRegex(ImportRowError, 'включите журнал') as error:
            self.s['imports'].operations(raw, 'broker', self.aid)
        self.assertEqual(error.exception.line, 2)
        self.assertEqual(self.dump(), before)

        self.prepare(0);aid,cash,stock=self.target()
        for index,value in ((3,cash),(3,999),(4,'USD')):
            row=self.deposit();row[index]=value
            with self.assertRaises(RecordError):self.run_import(self.rows([row]))
        self.assertEqual(self.row(self.cash)['quantity'],0)

    def test_batch_date_order_and_transfer_cost(self):
        self.prepare(0);aid,cash,stock=self.target()
        self.today+=timedelta(days=1)
        purchase=self.buy(day=self.today.isoformat())
        transfer=['t',self.today.isoformat(),'transfer',self.hid,'RUB',1,20,0,'',stock,'Transfer']
        self.run_import(self.rows([purchase,self.deposit(),transfer]))
        self.assertEqual(self.row(self.hid)['quantity'],1);self.assertEqual(self.row(stock)['avg_price'],D('20.5'))
        self.assertEqual(self.s['reports'].dashboard()['total']['invested'],100)

    def test_reconcile_exact_dated_balances_missing_rows_and_no_mutations(self):
        self.prepare(0);self.run_import(self.rows([self.deposit(amount='0.3000000000000000001')]))
        old_day=self.day;self.today+=timedelta(days=1)
        self.run_import(self.rows([self.deposit(key='next',amount='2',day=self.today.isoformat())]))
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        result=self.s['imports'].balances(csv_bytes(BALANCES,[[self.cash,'RUB','0.3']]),self.aid,old_day)
        cash=next(r for r in result if r['holding_id']==self.cash)
        self.assertEqual(cash['difference'],D('0.0000000000000000001'))
        self.assertEqual(cash['status'],'difference');self.assertIn('missing',[r['status'] for r in result])
        result=self.s['imports'].balances(csv_bytes(BALANCES,[[self.cash,'RUB','2.3000000000000000001'],[self.hid,'RUB',0]]),self.aid,self.today.isoformat())
        self.assertCountEqual([(r['holding_id'], r['status']) for r in result],
                              [(self.cash, 'match'), (self.hid, 'match')])
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)

    def test_reconcile_rejects_future_preopening_duplicate_wrong_currency_and_negative(self):
        self.prepare()
        service=self.s['imports'];raw=csv_bytes(BALANCES,[[self.cash,'RUB',1000]])
        for day in ('bad',(self.today-timedelta(days=1)).isoformat(),(self.today+timedelta(days=1)).isoformat()):
            with self.assertRaises((ValueError,RecordError)):service.balances(raw,self.aid,day)
        with self.assertRaises(RecordError):service.balances(raw,999,self.day)
        for rows in ([[999,'RUB',0]],[[self.cash,'USD',1000]],[[self.cash,'RUB',-1]],[[self.cash,'RUB',0]]*2):
            with self.assertRaises(RecordError):service.balances(csv_bytes(BALANCES,rows),self.aid,self.day)

    def test_web_preview_confirm_reconcile_templates_localization_and_escaping(self):
        self.prepare(0)
        for lang,label in [('ru','Импорт и сверка'),('en','Import and reconciliation')]:
            self.s['change_language'].execute(lang)
            r=self.client.get('/imports');self.assertEqual(r.status_code,200);self.assertIn(label,r.get_data(as_text=True))
        self.assertEqual(self.client.get('/imports/template/operations').data,(','.join(OPERATIONS)+'\n').encode())
        self.assertEqual(self.client.get('/imports/template/balances').status_code,200)
        self.assertEqual(self.client.get('/imports/template/bad').status_code,404)
        row=self.deposit();row[-1]='<script>alert(1)</script>';raw=self.rows([row])
        before=self.dump();disk=Path(self.vault.path).read_bytes()
        response=self.client.post('/imports/preview',data={'file':(io.BytesIO(raw),'broker.csv'),'account_id':self.aid,'source':'broker'})
        self.assertEqual(response.status_code,200);body=response.get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>',body);self.assertIn('&lt;script&gt;',body)
        self.assertEqual(self.dump(),before);self.assertEqual(Path(self.vault.path).read_bytes(),disk)
        token=html.unescape(re.search(r'name="token" value="([^"]+)"',body).group(1))
        self.assertEqual(self.client.post('/imports/confirm',data={'token':token}).status_code,302)
        self.assertEqual(self.row(self.cash)['quantity'],100)
        self.assertEqual(self.client.post('/imports/confirm',data={'token':token}).status_code,400)
        response=self.client.post('/imports/reconcile',data={'file':(io.BytesIO(csv_bytes(BALANCES,[[self.cash,'RUB',100]])),'balances.csv'),'account_id':self.aid,'date':self.day})
        self.assertEqual(response.status_code,200);self.assertIn('Match',response.get_data(as_text=True))
        self.assertEqual(self.client.post('/imports/preview',data={'account_id':self.aid}).status_code,400)
        bad=self.deposit();bad[3]=999
        response=self.client.post('/imports/preview',data={'file':(io.BytesIO(self.rows([bad])),'bad.csv'),'account_id':self.aid,'source':'broker'})
        self.assertEqual(response.status_code,400);self.assertIn('Row 2:',response.get_data(as_text=True))
        response=self.client.post('/imports/preview',data={'file':(io.BytesIO(raw),'x.csv'),'account_id':'invalid','source':'broker'})
        self.assertEqual(response.status_code,400);self.assertIn('Check the account',response.get_data(as_text=True))
        self.assertEqual(self.client.post('/imports/confirm',data={'token':'tampered'}).status_code,400)
        self.assertEqual(self.client.post('/imports/preview',data=b'x'*(3*MAX_BYTES+1),content_type='application/octet-stream').status_code,413)

    def test_preview_token_is_bound_to_vault_session_and_expires(self):
        self.prepare(0)
        def token():
            r=self.client.post('/imports/preview',data={'file':(io.BytesIO(self.rows([self.deposit()])),'x.csv'),'account_id':self.aid,'source':'broker'})
            return html.unescape(re.search(r'name="token" value="([^"]+)"',r.get_data(as_text=True)).group(1))
        from itsdangerous.timed import TimestampSigner
        original=TimestampSigner.get_timestamp
        def expired_timestamp(signer):
            return original(signer)-(3601 if signer.salt==b'statement-preview-v1' else 0)
        with patch.object(TimestampSigner,'get_timestamp',expired_timestamp):
            expired=token()
        self.assertEqual(self.client.post('/imports/confirm',data={'token':expired}).status_code,400)
        old=token()
        self.vault.lock();self.vault.unlock('password123')
        with self.client.session_transaction() as session:session['vault']=self.vault.token
        self.assertEqual(self.client.post('/imports/confirm',data={'token':old}).status_code,400)
