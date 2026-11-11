from dataclasses import dataclass
from typing import Optional
from wealth_manager.shared.records import required, country_code, website, RecordError

STATUSES = {'watch':'Наблюдаю','owned':'В портфеле','archived':'Архив'}

@dataclass(frozen=True)
class Company:
    name: str
    ticker: str = ''
    exchange: str = ''
    country: str = 'RU'
    sector: str = ''
    website: str = ''
    thesis: str = ''
    status: str = 'watch'
    id: Optional[int] = None

    def validate(self):
        required(self.name)
        country_code(self.country)
        website(self.website)
        if self.status not in STATUSES: raise RecordError('Некорректный статус')
        if bool(self.ticker) != bool(self.exchange): raise RecordError('Укажите и тикер, и биржу либо оставьте оба поля пустыми')
