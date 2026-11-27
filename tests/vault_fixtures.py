"""Isolated fixtures for business tests. No application config or data is shared."""
from datetime import date
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import db
from vault import Vault
from wealth_manager.adapters.persistence.decimals import configure
from wealth_manager.bootstrap import build_services
from wealth_manager.factory import create_app
from wealth_manager.modules.accounts.domain import Account
from wealth_manager.modules.companies.domain import Company
from wealth_manager.modules.ledger.domain import Entry
from wealth_manager.modules.macroeconomics.domain import Observation
from wealth_manager.modules.planning.domain import Goal, Portfolio
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.taxes.domain import Deduction, TaxEntry


def historical_schema(connection, version):
    """Build the starting schema; callers exercise the upgrade via db.init_db."""
    steps = (
        db._init_legacy,          # v3
        db._migrate_entities, db._migrate_planning, db._migrate_research,
        db._migrate_classifications, db._migrate_valuation_dates, db._migrate_history,
        db._migrate_decimals, db._migrate_ledger, db._migrate_liabilities,
        db._migrate_tax_documents, db._migrate_goal_forecasts, db._migrate_instruments,
        db._migrate_macro_versions, db._migrate_macro_calculations, db._migrate_economic_cycle,
        db._migrate_market_catalog, db._migrate_sector_scoring,  # v20
    )
    if not 3 <= version <= 2 + len(steps):
        raise ValueError(f'Unsupported historical fixture version {version}')
    for step in steps[:version - 2]:
        step(connection)
    actual = connection.execute('PRAGMA user_version').fetchone()[0]
    if actual != version:
        raise AssertionError(f'Expected starting schema {version}, got {actual}')
    connection.execute('PRAGMA foreign_keys=ON')
    configure(connection)


class VaultCase(unittest.TestCase):
    # Only the work factor used to create a disposable business fixture changes.
    # Real KDF, encryption, writes, reopen and rollback all run normally.
    scrypt_parameters = (2 ** 10, 8, 1)
    schema_version = None

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.vault = Vault(str(Path(directory.name) / 'test.enc'))
        with patch('vault.SCRYPT', self.scrypt_parameters):
            connection = self.vault.create('password123')
        self.addCleanup(self.vault.lock)
        if self.schema_version is None:
            db.init_db(connection)
        else:
            historical_schema(connection, self.schema_version)
        self.vault.save(force=True)
        self.today = date.today()
        self.day = self.today.isoformat()
        self.s = build_services(self.vault, today=lambda: self.today)

    def dump(self):
        return list(self.vault.con.iterdump())

    @cached_property
    def app(self):
        return create_app({'TESTING': True}, vault=self.vault)

    @cached_property
    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['vault'] = self.vault.token
        return client


class PortfolioCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.aid = self.s['save_financial_account'].execute(Account('Broker'))
        self.hid = self.s['positions'].save(Position(self.aid, name='Known stock', quantity=2, price=50, avg_price=40))
        self.gid = self.s['planning'].save_goal(Goal('Goal', Decimal('1000')))
        self.pid = self.s['planning'].save_portfolio(Portfolio('Portfolio', self.gid))


class ResearchCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.company = Company('Example', 'EXM', 'MOEX', 'RU', website='https://example.com', thesis='Long term')
        self.observation = Observation('RU', 'key_rate', '2026-01-01', Decimal('10.5'), 'Manual source')
        self.entry = TaxEntry(2026, 'RU', 'Salary', 'RUB', Decimal('100000'), Decimal('13'), Decimal('5000'))
        self.deduction = Deduction(2026, 'RU', 'Education', 'RUB', Decimal('10000'), Decimal('1300'),
                                   documents='Invoice 123; home folder')


class LedgerCase(PortfolioCase):
    def prepare(self, amount=1000):
        self.s['positions'].save(Position(self.aid, id=self.hid, name='Stock', ticker='ABC', quantity=0, price=20))
        self.cash = self.s['positions'].save(Position(self.aid, name='Cash', asset_class_code='cash', quantity=amount))
        self.cmd = self.s['ledger']
        self.cmd.activate(self.aid)

    def row(self, holding_id):
        return dict(self.vault.con.execute('SELECT * FROM holdings WHERE id=?', (holding_id,)).fetchone())

    def entry(self, kind='buy', **changes):
        values = dict(date=self.today.isoformat(), kind=kind, holding_id=self.hid, quantity=Decimal('10'),
                      price=Decimal('20'), fee=Decimal('2'), cash_id=self.cash, request_id=kind)
        values.update(changes)
        return Entry(**values)

    def target(self):
        account = self.s['save_financial_account'].execute(Account('Destination'))
        cash = self.s['positions'].save(Position(account, name='Cash', asset_class_code='cash'))
        stock = self.s['positions'].save(Position(account, name='Stock', ticker='ABC', price=20))
        self.cmd.activate(account)
        return account, cash, stock
