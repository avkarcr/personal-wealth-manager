"""Composition of source adapters, preserving cancellation between requests."""
from .cbr import Provider as Cbr
from .moex import Provider as Moex

class Providers:
    def __init__(self):self.cbr,self.moex=Cbr(),Moex()
    def fetch(self,kind,start,end,cancel=lambda:False):
        return self.cbr.fetch(kind,start,end) if kind in ('key_rate','ofz_curve') else self.moex.fetch(kind,start,end,cancel)
