"""Append-only revisions and a compatibility projection for manual observations."""
from dataclasses import asdict
import hashlib
import json
from decimal import Decimal
from wealth_manager.modules.macroeconomics.data import Indicator,Series,instant,utcnow,INDICATOR_EN
from wealth_manager.shared.decimal import exact_text
from wealth_manager.shared.records import RecordError
from wealth_manager.shared.errors import EntityNotFound


def canonical(data):return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=lambda v:exact_text(v) if isinstance(v,Decimal) else str(v))
def digest(text):return hashlib.sha256(text.encode()).hexdigest()

class Repository:
    def __init__(self,con,clock=None):self.con,self.clock=con,clock or utcnow
    def series(self,source_id,definition,series):
        self.con.execute('INSERT OR IGNORE INTO macro_indicators(code,name_ru,name_en) VALUES (?,?,?)',(definition.code,definition.name_ru,definition.name_en))
        data=asdict(series);data['scale']=exact_text(series.scale)
        key=digest(canonical(dict(data,source_id=source_id)))
        self.con.execute('INSERT OR IGNORE INTO macro_series(identity,source_id,indicator,country,provider_key,unit,frequency,scale,adjustment,base_year,methodology) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (key,source_id,series.indicator,series.country,series.provider_key,series.unit,series.frequency,data['scale'],series.adjustment,series.base_year,series.methodology))
        return self.con.execute('SELECT id FROM macro_series WHERE identity=?',(key,)).fetchone()[0]
    def artifact(self,source_id,locator,content,parser,media,received):
        checksum=hashlib.sha256(content).hexdigest();key=digest(canonical([source_id,locator,checksum,parser,media]))
        self.con.execute('INSERT OR IGNORE INTO macro_artifacts(identity,source_id,locator,digest,content,parser_version,media_type,received_at) VALUES (?,?,?,?,?,?,?,?)',(key,source_id,locator,checksum,content,parser,media,received))
        return self.con.execute('SELECT id FROM macro_artifacts WHERE identity=?',(key,)).fetchone()[0]
    def refresh_state(self):
        from wealth_manager.modules.macroeconomics.scheduling import defaults
        row=self.con.execute("SELECT value FROM settings WHERE key='macro_refresh_v1'").fetchone()
        return json.loads(row[0]) if row else defaults()
    def save_refresh_state(self,state):
        self.con.execute("INSERT INTO settings(key,value) VALUES ('macro_refresh_v1',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(state),))
    def revision_token(self):
        return self.con.execute("SELECT COALESCE(MAX(id),0) FROM macro_revisions").fetchone()[0]
    def latest(self,rid):
        row=self.con.execute('SELECT * FROM macro_revisions WHERE record_id=? ORDER BY id DESC LIMIT 1',(rid,)).fetchone()
        return dict(row) if row else None
    def revision(self,rid,sid,start,end,value,published,precision,status,note,aid,received,deleted=False,deduplicate=False):
        available=instant(published,precision=='date') if published else received
        data=dict(series_id=sid,period_start=start,period_end=end,value=exact_text(value),published_at=published,
                  publication_precision=precision,status=status,note=note,artifact_id=aid,deleted=int(deleted))
        old=self.latest(rid)
        if deduplicate and old:
            # Page chrome, retrieval range and artifact hash are not economic revisions.
            if all(old[k]==v for k,v in data.items() if k!='artifact_id'):return old['id']
        fingerprint=digest(canonical(data))
        if old and old['fingerprint']==fingerprint:return old['id']
        # Official packets are immutable evidence: redownloading an old revision must not resurrect it.
        previous=self.con.execute('SELECT id FROM macro_revisions WHERE record_id=? AND fingerprint=? ORDER BY id DESC LIMIT 1',(rid,fingerprint)).fetchone()
        if previous and deduplicate:return previous[0]
        cols=tuple(data)
        return self.con.execute('INSERT INTO macro_revisions(record_id,'+','.join(cols)+',retrieved_at,available_at,fingerprint,previous_revision_id) VALUES ('+','.join('?' for _ in range(len(cols)+5))+')',
                                (rid,)+tuple(data.values())+(received,available,fingerprint,old['id'] if old else None)).lastrowid
    def ingest(self,definition,source,series,artifact,points):
        now=instant(self.clock())
        self.con.execute('INSERT OR IGNORE INTO macro_sources(key,title,kind,url,terms) VALUES (?,?,?,?,?)',(source.key,source.title,source.kind,source.url,source.terms))
        src=self.con.execute('SELECT * FROM macro_sources WHERE key=?',(source.key,)).fetchone()
        if any(src[k]!=getattr(source,k) for k in ('title','kind','url','terms')):raise RecordError('Метаданные источника изменились: требуется отдельная версия источника')
        sid=self.series(src['id'],definition,series)
        aid=self.artifact(src['id'],artifact.locator,artifact.content,artifact.parser_version,artifact.media_type,now)
        ids=[]
        for p in points:
            key=digest(canonical([sid,p.period_start,p.period_end]))
            if not self.con.execute('SELECT 1 FROM macro_records WHERE origin_key=?',(key,)).fetchone():
                self.con.execute('INSERT INTO macro_records(origin_key) VALUES (?)',(key,))
            rid=self.con.execute('SELECT id FROM macro_records WHERE origin_key=?',(key,)).fetchone()[0]
            published=instant(p.published_at) if p.publication_precision=='instant' else p.published_at
            ids.append(self.revision(rid,sid,p.period_start,p.period_end,p.value,published,p.publication_precision,p.status,p.note,aid,now,deduplicate=True))
        return ids
    def save(self,record,received=None):
        record.validate();now=instant(received or self.clock())
        if self.con.execute('SELECT 1 FROM macro_observations WHERE country=? AND indicator=? AND date=? AND id<>?',(record.country,record.indicator,record.date,record.id or -1)).fetchone():raise RecordError('Показатель за эту дату уже есть: отредактируйте запись')
        if record.id is not None:
            old=self.latest(record.id)
            if not old or old['deleted'] or not self.con.execute('SELECT 1 FROM macro_observations WHERE id=?',(record.id,)).fetchone():raise EntityNotFound(record.id)
        from wealth_manager.modules.macroeconomics.domain import INDICATORS
        source_id=self.con.execute("SELECT id FROM macro_sources WHERE key='legacy_manual'").fetchone()[0]
        sid=self.series(source_id,Indicator(record.indicator,INDICATORS[record.indicator],INDICATOR_EN[record.indicator]),Series(record.indicator,record.country,record.indicator,'percent','unknown',methodology='legacy_unknown'))
        raw=canonical({k:v for k,v in asdict(record).items() if k!='id'}).encode()
        aid=self.artifact(source_id,record.source,raw,'manual-v1','application/json',now)
        rid=record.id
        if rid is None:rid=self.con.execute('INSERT INTO macro_records(origin_key) VALUES (NULL)').lastrowid
        self.revision(rid,sid,record.date,record.date,record.value,'','unknown','unknown',record.note,aid,now)
        return rid
    def delete(self,rid):
        old=self.latest(rid)
        if not old or old['deleted'] or not self.con.execute('SELECT 1 FROM macro_observations WHERE id=?',(rid,)).fetchone():raise EntityNotFound(rid)
        self.revision(rid,old['series_id'],old['period_start'],old['period_end'],old['value'],'','unknown',old['status'],old['note'],old['artifact_id'],instant(self.clock()),True)

class Reader:
    def __init__(self,vault):self.vault=vault
    def read(self):
        with self.vault.lock_:
            if not self.vault.unlocked:raise RuntimeError('Vault is locked')
            rows=[dict(r) for r in self.vault.con.execute('''SELECT r.id AS revision_id,r.record_id,r.period_start,r.period_end,r.value,r.published_at,r.publication_precision,r.retrieved_at,r.available_at,r.status,r.note,r.deleted,r.previous_revision_id,
             s.id AS series_id,s.indicator,s.country,s.unit,s.frequency,s.scale,s.adjustment,s.base_year,s.methodology,
             i.name_ru,i.name_en,src.title AS source_title,src.kind AS source_kind,a.locator,a.digest,a.parser_version
             FROM macro_revisions r JOIN macro_series s ON s.id=r.series_id JOIN macro_indicators i ON i.code=s.indicator
             JOIN macro_sources src ON src.id=s.source_id JOIN macro_artifacts a ON a.id=r.artifact_id ORDER BY r.id DESC''')]
            for r in rows:r['value']=Decimal(r['value']);r['scale']=Decimal(r['scale'])
            return rows
