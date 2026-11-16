from dataclasses import asdict
from .operations import get_row

DOCUMENT_FIELDS='id,entry_id,deduction_id,filename,mime,digest,size,date,number,issuer,note'

class TaxEvidenceRepository:
    def __init__(self,connection):self.connection=connection
    def entry(self,rid):return get_row(self.connection,'tax_entries',rid)
    def deduction(self,rid):return get_row(self.connection,'tax_deductions',rid)
    def payments(self,rid):return [dict(r) for r in self.connection.execute('SELECT * FROM tax_payments WHERE entry_id=? ORDER BY date,id',(rid,))]
    def payment(self,rid):return get_row(self.connection,'tax_payments',rid)
    def find_payment(self,key):
        row=self.connection.execute('SELECT * FROM tax_payments WHERE request_id=?',(key,)).fetchone()
        return dict(row) if row else None
    def add_payment(self,payment):return self._insert('tax_payments',asdict(payment))
    def void_payment(self,pid):self.connection.execute('UPDATE tax_payments SET voided=1 WHERE id=?',(pid,))
    def documents(self,entry_id=None,deduction_id=None):
        return [dict(r) for r in self.connection.execute('SELECT '+DOCUMENT_FIELDS+' FROM tax_documents WHERE entry_id=? OR deduction_id=?',(entry_id,deduction_id))]
    def document(self,did):return get_row(self.connection,'tax_documents',did)
    def size(self):return self.connection.execute('SELECT COALESCE(SUM(size),0) FROM tax_documents').fetchone()[0]
    def add_document(self,document):return self._insert('tax_documents',document)
    def delete_document(self,did):self.connection.execute('DELETE FROM tax_documents WHERE id=?',(did,))
    def _insert(self,table,data):
        return self.connection.execute('INSERT INTO '+table+' ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',tuple(data.values())).lastrowid
