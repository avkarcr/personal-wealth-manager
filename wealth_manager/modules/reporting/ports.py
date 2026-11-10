from typing import Protocol

class ReportRepository(Protocol):
    def read(self) -> dict: ...
