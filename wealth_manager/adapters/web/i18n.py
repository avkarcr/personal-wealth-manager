"""Presentation-only translation; stored user content and domain codes stay intact."""
import json
from pathlib import Path
from flask import g, session
from wealth_manager.adapters.persistence.sqlite import SqlitePreferencesRepository

EN = json.loads((Path(__file__).parent / 'locales' / 'en.json').read_text(encoding='utf-8'))

def gettext(message):
    if getattr(g, 'language', 'ru') == 'en':
        return EN.get(message, message)
    return message


def install_i18n(app, vault):
    @app.before_request
    def prepare_request():
        language = session.get('language', 'ru')
        if vault.unlocked:
            language = SqlitePreferencesRepository(vault.con).get_language()
            session['language'] = language
        g.language = language if language in ('ru', 'en') else 'ru'

    @app.context_processor
    def translations():
        return {'_': gettext, 'language': getattr(g, 'language', 'ru'),
                'ui_messages': EN if getattr(g, 'language', 'ru') == 'en' else {}}
    app.jinja_env.filters['translate'] = gettext
