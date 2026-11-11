"""Composition root: only this layer selects concrete adapters."""
from wealth_manager.adapters.persistence.unit_of_work import VaultUnitOfWork
from wealth_manager.modules.accounts.application import SaveAccount, SaveFinancialAccount
from wealth_manager.modules.assets.application import SaveAsset
from wealth_manager.modules.income_sources.application import SaveIncomeSource
from wealth_manager.modules.preferences.application import ChangeLanguage

from datetime import date
from wealth_manager.modules.accounts.commands import AccountCommands
from wealth_manager.modules.positions.application import PositionCommands
from wealth_manager.modules.cashflows.application import CashflowCommands
from wealth_manager.modules.rentals.application import RentalCommands


def build_services(vault, *, today=date.today, http=None, catalog=None):
    import json
    from pathlib import Path
    from wealth_manager.adapters.persistence.reports import SqliteReportRepository
    from wealth_manager.modules.reporting.application import Reports
    from wealth_manager.modules.market_data.application import MarketData
    from wealth_manager.modules.market_data.domain import ExchangeCatalog
    from wealth_manager.adapters.market_data.providers import HttpClient, CryptoProvider, MoexProvider, CbrProvider
    http = http or HttpClient()
    catalog = catalog or ExchangeCatalog(json.loads((Path(__file__).parent / 'adapters/market_data/exchanges.json').read_text()))
    from wealth_manager.modules.planning.application import PlanningCommands
    from wealth_manager.modules.planning.reports import PlanningReports
    from wealth_manager.adapters.persistence.planning import SqlitePlanningReader
    from wealth_manager.modules.companies.application import Commands as CompanyCommands
    from wealth_manager.modules.companies.reports import Reports as CompanyReports
    from wealth_manager.modules.macroeconomics.application import Commands as MacroCommands
    from wealth_manager.modules.macroeconomics.reports import Reports as MacroReports
    from wealth_manager.modules.taxes.application import Commands as TaxCommands
    from wealth_manager.modules.taxes.reports import Reports as TaxReports
    from wealth_manager.adapters.persistence.research import CompanyReader, ObservationReader, TaxReader
    reports = SqliteReportRepository(vault)
    factory = lambda: VaultUnitOfWork(vault)
    return {"companies": CompanyCommands(factory), "company_reports": CompanyReports(CompanyReader(vault)),
            "macroeconomics": MacroCommands(factory), "macro_reports": MacroReports(ObservationReader(vault)),
            "taxes": TaxCommands(factory), "tax_reports": TaxReports(TaxReader(vault)),
            "planning": PlanningCommands(factory), "planning_reports": PlanningReports(SqlitePlanningReader(vault)),
            "save_financial_account": SaveFinancialAccount(factory), "save_asset": SaveAsset(factory),
            "save_income_source": SaveIncomeSource(factory), "reports": Reports(reports, today),
            "market_data": MarketData(factory,reports,catalog,CryptoProvider(http),MoexProvider(http),CbrProvider(http),today),
            "save_account": SaveAccount(factory), "change_language": ChangeLanguage(factory),
            "accounts": AccountCommands(factory), "positions": PositionCommands(factory, today),
            "cashflows": CashflowCommands(factory, today), "rentals": RentalCommands(factory, today)}


def build_runtime(config, *, vault=None):
    from vault import Vault
    from wealth_manager.adapters.runtime import ApplicationRuntime

    if vault is None:
        vault = Vault(config["VAULT_PATH"], idle_minutes=int(config["IDLE_MINUTES"]))
    return ApplicationRuntime(vault, build_services(vault), poll_seconds=config["AUTOLOCK_POLL_SECONDS"])
