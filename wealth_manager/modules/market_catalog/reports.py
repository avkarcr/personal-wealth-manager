"""Current catalog views. Classification and listing metadata do not value holdings."""
from datetime import date
from .domain import find,SECURITY_TYPES,LISTING_STATUSES
from .ports import Reader


def sector_views(data):
    rows=data['sectors'];result=[]
    for root in sorted((r for r in rows if r['parent_id'] is None),key=lambda r:r['name'].casefold()):
        children=sorted((r for r in rows if r['parent_id']==root['id']),key=lambda r:r['name'].casefold())
        for r in [root]+children:
            path=(root['name']+' / ' if r['parent_id'] else '')+r['name']
            path_en=((root['name_en'] or root['name'])+' / ' if r['parent_id'] else '')+(r['name_en'] or r['name'])
            ids={r['id']} | ({s['id'] for s in children} if r['parent_id'] is None else set())
            companies={m['company_id'] for m in data['memberships'] if m['sector_id'] in ids}
            result.append(dict(r,path=path,path_en=path_en,company_ids=sorted(companies),available=not (r['archived'] or root['archived'])))
    return result


def security_view(data,sid,today):
    security=find(data['securities'],sid);listings=[]
    for row in sorted((r for r in data['listings'] if r['security_id']==sid),key=lambda r:(r['valid_from'],r['id']),reverse=True):
        listings.append(dict(row,ended=bool(row['valid_to'] and row['valid_to']<today),future=bool(row['valid_from'] and row['valid_from']>today),
                             events=sorted((e for e in data['events'] if e['listing_id']==row['id']),key=lambda e:e['id'],reverse=True)))
    return dict(security,company=find(data['companies'],security['company_id']),listings=listings,
                underlying=find(data['securities'],security['underlying_id']) if security['underlying_id'] else None,
                used=any(l['security_id']==sid for l in data['links']),
                receipts=[s for s in data['securities'] if s['underlying_id']==sid])


def company_catalog(data,cid,today):
    sectors=sector_views(data);members=[]
    for m in data['memberships']:
        if m['company_id']==cid:members.append(dict(m,sector=find(sectors,m['sector_id'])))
    return dict(securities=[security_view(data,r['id'],today) for r in data['securities'] if r['company_id']==cid],
                memberships=sorted(members,key=lambda m:(not m['is_primary'],m['sector']['path'])),sectors=sectors,
                underlying_choices=[r for r in data['securities'] if r['kind'] in ('common_share','preferred_share')],
                security_types=SECURITY_TYPES,listing_statuses=LISTING_STATUSES)


class Reports:
    def __init__(self,reader: Reader,today=date.today):self.reader,self.today=reader,today
    def security(self,rid):return security_view(self.reader.read(),rid,self.today().isoformat())
    def listing(self,rid):return find(self.reader.read()['listings'],rid)
    def overview(self):
        data=self.reader.read();sectors=sector_views(data)
        for sector in sectors:sector['companies']=[c for c in data['companies'] if c['id'] in sector['company_ids']]
        return dict(sectors=sectors,roots=[s for s in sectors if s['parent_id'] is None],
                    unclassified=[c for c in data['companies'] if not any(m['company_id']==c['id'] for m in data['memberships'])])
