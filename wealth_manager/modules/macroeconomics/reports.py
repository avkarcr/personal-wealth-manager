from .ports import Reader
from .domain import INDICATORS, summarize

class Reports:
    def __init__(self,reader: Reader): self.reader=reader
    def overview(self):
        rows=sorted(self.reader.read(),key=lambda r:(r['date'],r['id']),reverse=True)
        return dict(observations=rows,indicators=INDICATORS,summary=summarize(rows))
