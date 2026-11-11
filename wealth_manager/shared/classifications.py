"""Stable built-in identifiers; custom text is kept in a separate field."""
ASSET_LABELS = {'stocks': 'Акции', 'bonds': 'Облигации', 'funds': 'Фонды', 'cash': 'Деньги', 'gold': 'Золото', 'crypto': 'Крипто', 'stablecoins': 'Стейблкоины', 'other': 'Прочее'}
CATEGORY_LABELS = {'dividends': 'Дивиденды', 'coupons': 'Купоны', 'cash_interest': 'Проценты на остаток', 'iis_deduction': 'Налоговый вычет (ИИС)', 'broker_fee': 'Комиссия брокера', 'tax': 'Налог', 'account_service': 'Обслуживание счёта', 'staking': 'Стейкинг', 'farming': 'Фарминг / LP', 'airdrop': 'Аирдроп', 'launchpool': 'Лаунчпул', 'earn': 'Earn / сбережения', 'p2p': 'P2P', 'trading': 'Трейдинг', 'referrals': 'Рефералы', 'nodes': 'Ноды / валидатор', 'exchange_fee': 'Комиссия биржи', 'gas': 'Газ (комиссия сети)', 'p2p_fee': 'Комиссия P2P', 'rent': 'Аренда', 'daily_rent': 'Посуточная аренда', 'utilities': 'Коммунальные платежи', 'repairs': 'Ремонт', 'mortgage_interest': 'Ипотека — проценты', 'insurance': 'Страховка', 'interest': 'Проценты', 'income': 'Доход', 'fee': 'Комиссия', 'project': 'Проект', 'hourly_work': 'Почасовая работа', 'subscription': 'Абонентская оплата', 'bonus': 'Бонус', 'consulting': 'Консультация', 'platform_fee': 'Комиссия площадки', 'npd_tax': 'Налог (НПД)', 'software': 'Софт и сервисы', 'conversion': 'Вывод / конвертация'}
ASSET_ALIASES = {label:code for code,label in ASSET_LABELS.items()}
CATEGORY_ALIASES = {label:code for code,label in CATEGORY_LABELS.items()}


def asset_key(row):
    code=row['asset_class_code'] if 'asset_class_code' in row.keys() else None
    if code is None: code=ASSET_ALIASES.get(row['asset_class'],'custom')
    return 'custom:'+row['asset_class'] if code=='custom' else code


def category_key(row):
    if 'category_code' in row:
        return row['category_code'] or ('custom:'+row['category'] if row['category'] else 'uncategorized')
    return CATEGORY_ALIASES.get(row['category']) or ('custom:'+row['category'] if row['category'] else 'uncategorized')
