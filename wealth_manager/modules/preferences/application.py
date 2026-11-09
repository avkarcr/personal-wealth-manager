from typing import Callable, Protocol

SUPPORTED_LANGUAGES = ("ru", "en")

class PreferencesRepository(Protocol):
    def get_language(self) -> str: ...
    def set_language(self, language: str) -> None: ...

class PreferencesUnitOfWork(Protocol):
    preferences: PreferencesRepository
    def __enter__(self) -> "PreferencesUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...

class ChangeLanguage:
    def __init__(self, uow_factory: Callable[[], PreferencesUnitOfWork]):
        self.uow_factory = uow_factory

    def execute(self, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("Unsupported language")
        with self.uow_factory() as uow:
            uow.preferences.set_language(language)
            uow.commit()
