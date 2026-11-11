from .ports import Reader
from .domain import STATUSES

class Reports:
    def __init__(self,reader: Reader): self.reader=reader
    def overview(self):
        return dict(companies=sorted(self.reader.read(),key=lambda r:(r['status']=='archived',r['name'].casefold())),statuses=STATUSES)
