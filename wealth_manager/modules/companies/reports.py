"""Explore the existing issuer catalog by classification, identity and available data."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from wealth_manager.modules.market_catalog.reports import company_catalog, sector_views
from .domain import STATUSES
from .ports import Reader


SORT_OPTIONS = {
    'name': 'Название (А–Я)',
    'name_desc': 'Название (Я–А)',
    'turnover': 'Оборот выпуска TQBR',
}


def identifiers(company):
    yield company['name']
    yield company['ticker']
    for issue in company['catalog']['securities']:
        yield issue['name']
        yield issue['isin']
        yield issue['registration_number']
        for listing in issue['listings']:
            yield listing['ticker']
            if listing['iss']:
                reference = listing['iss'].reference
                yield reference.name
                yield reference.isin
                yield reference.registration_number


def turnover_quotes(company):
    if company['status'] == 'archived':
        return
    for issue in company['catalog']['securities']:
        if issue['archived']:
            continue
        for listing in issue['listings']:
            if listing['future'] or listing['ended'] or listing['status'] in ('suspended', 'delisted'):
                continue
            quote = listing['iss'].quote if listing['iss'] else None
            if quote is not None and quote.turnover is not None:
                yield quote


class Reports:
    def __init__(self, reader: Reader, today=date.today):
        self.reader = reader
        self.today = today

    def overview(self, *, query='', sector='', status='', scope='', order='name'):
        data = self.reader.read()
        today = self.today().isoformat()
        sectors = sector_views(data)
        query = query.strip()
        term = query.casefold()
        status = status if status in STATUSES else ''
        scope = scope if scope == 'moex' else ''
        order = order if order in SORT_OPTIONS else 'name'

        sector_members = {str(row['id']): set(row['company_ids']) for row in sectors}
        classified = {row['company_id'] for row in data['memberships']}
        sector_members['unclassified'] = {row['id'] for row in data['companies']} - classified
        selected = sector_members.get(sector, set())
        portfolios = defaultdict(dict)
        for link in data['portfolio_links']:
            if Decimal(link['quantity']) != 0:
                portfolios[link['company_id']][link['id']] = dict(id=link['id'], name=link['name'])

        companies = []
        for row in data['companies']:
            cid = row['id']
            if (sector and cid not in selected) or (status and row['status'] != status):
                continue
            catalog = company_catalog(data, cid, today, sectors=sectors)
            if scope and not any(listing['exchange'].upper() == 'MOEX'
                                 for issue in catalog['securities'] for listing in issue['listings']):
                continue
            company = dict(row, catalog=catalog, portfolios=sorted(portfolios[cid].values(),
                           key=lambda p: (p['name'].casefold(), p['id'])))
            if not term or any(term in value.casefold() for value in identifiers(company)):
                companies.append(company)

        companies.sort(key=lambda row: (row['name'].casefold(), row['id']), reverse=order == 'name_desc')
        companies.sort(key=lambda row: row['status'] == 'archived')
        turnover_day = None
        if order == 'turnover':
            quotes = {row['id']: list(turnover_quotes(row)) for row in companies}
            turnover_day = max((quote.day for items in quotes.values() for quote in items), default=None)
            totals = {cid: max((quote.turnover for quote in items if quote.day == turnover_day), default=None)
                      for cid, items in quotes.items()}
            companies.sort(key=lambda row: (totals[row['id']] is None, -(totals[row['id']] or 0)))

        return dict(companies=companies, statuses=STATUSES, sectors=sectors, sort_options=SORT_OPTIONS,
                    turnover_day=turnover_day, missing_sector=bool(sector and sector not in sector_members),
                    filters=dict(query=query, sector=sector, status=status, scope=scope, order=order))
