from .ports import Reader
from .domain import STATUSES, estimate, summarize

class Reports:
    def __init__(self,reader: Reader): self.reader=reader
    def overview(self):
        data=self.reader.read()
        entries=[dict(r,estimate=estimate(r)) for r in data['entries']]
        return dict(entries=entries,deductions=data['deductions'],statuses=STATUSES,summary=summarize(entries,data['deductions']))
