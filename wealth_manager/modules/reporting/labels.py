from wealth_manager.shared.classifications import ASSET_LABELS
KIND_LABELS = {"broker": "Брокерский счёт", "crypto": "Криптобиржа / кошелёк", "realty": "Недвижимость",
               "other": "Прочее", "freelance": "Фриланс"}
KIND_SHORT = {"broker": "Фондовый рынок", "crypto": "Крипто", "realty": "Недвижимость", "other": "Прочее", "freelance": "Фриланс"}
INVEST_KINDS = ("broker", "crypto", "realty", "other")
FLOW_LABELS = {"deposit": "Пополнение", "withdrawal": "Вывод", "income": "Доход", "expense": "Расход"}
ASSET_CLASSES = list(ASSET_LABELS)
STABLECOINS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD"}

# Подсказки категорий в форме операции (можно вводить и свои)
CATEGORIES = {'broker': {'income': ['dividends', 'coupons', 'cash_interest', 'iis_deduction'], 'expense': ['broker_fee', 'tax', 'account_service']}, 'crypto': {'income': ['staking', 'farming', 'airdrop', 'launchpool', 'earn', 'p2p', 'trading', 'referrals', 'nodes'], 'expense': ['exchange_fee', 'gas', 'tax', 'p2p_fee']}, 'realty': {'income': ['rent', 'daily_rent'], 'expense': ['utilities', 'repairs', 'tax', 'mortgage_interest', 'insurance']}, 'other': {'income': ['interest', 'income'], 'expense': ['fee', 'tax']}, 'freelance': {'income': ['project', 'hourly_work', 'subscription', 'bonus', 'consulting'], 'expense': ['platform_fee', 'npd_tax', 'software', 'conversion']}}



FREQ_LABELS = {"month": "ежемесячно", "quarter": "раз в квартал", "halfyear": "раз в полгода",
               "year": "раз в год", "day": "посуточно / разовые"}
FREQ_MONTHS = {"month": 1, "quarter": 3, "halfyear": 6, "year": 12}
RENT_STATUS = {"expected": "Ожидается", "agent": "У риелтора", "received": "Получено"}








