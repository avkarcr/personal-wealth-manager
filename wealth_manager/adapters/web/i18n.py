"""Presentation-only translation; stored user content and domain codes stay intact."""
import json
from pathlib import Path
from flask import g, session
from wealth_manager.adapters.persistence.sqlite import SqlitePreferencesRepository

from wealth_manager.shared.classifications import ASSET_LABELS, CATEGORY_LABELS, asset_key, category_key

EN = json.loads((Path(__file__).parent / 'locales' / 'en.json').read_text(encoding='utf-8'))

def gettext(message):
    if getattr(g, 'language', 'ru') == 'en':
        return EN.get(message, message)
    return message


def classification_label(key):
    if key.startswith('custom:'): return key[7:]
    return gettext({**ASSET_LABELS,**CATEGORY_LABELS,'real_estate':'Недвижимость','uncategorized':'Без категории'}.get(key,key))

def asset_label(row): return classification_label(asset_key(row))
def category_label(row):
    return classification_label(category_key(row)) if row.get('category_code') or row.get('category') else ''

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
        return {'_': gettext, 'asset_label':asset_label, 'category_label':category_label,
                'classification_label':classification_label, 'CATEGORY_LABELS':CATEGORY_LABELS, 'language': getattr(g, 'language', 'ru'),
                'ui_messages': EN if getattr(g, 'language', 'ru') == 'en' else {}}
    app.jinja_env.filters['translate'] = gettext
    app.jinja_env.filters['class_labels'] = lambda rows: [(classification_label(k),v) for k,v in rows]

