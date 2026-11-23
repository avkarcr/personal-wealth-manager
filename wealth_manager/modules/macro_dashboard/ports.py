from typing import Protocol
from wealth_manager.modules.economic_cycle.ports import Session as CycleSession


class Session(CycleSession, Protocol):
    def dashboard_snapshot(self, token: str) -> dict: ...
