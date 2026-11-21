"""Fetch outside the write transaction; validate a whole response before committing."""
from datetime import date
from wealth_manager.shared.records import RecordError, iso_date

class RefreshError(RecordError):pass

class Refresh:
    def __init__(self,provider,session,today=date.today):
        self.provider,self.session,self.today=provider,session,today
    def execute(self,kind,start,end,token):
        iso_date(start);iso_date(end)
        first,last=date.fromisoformat(start),date.fromisoformat(end)
        minimum={'key_rate':date(2013,9,17),'ofz_curve':date(2003,1,4)}
        if kind not in minimum or first<minimum[kind] or last<first or last>self.today() or (last-first).days>365:
            raise RefreshError('Выберите допустимый период ЦБ длиной не более 366 дней')
        self.session.check(token)
        bundles=self.provider.fetch(kind,first,last)
        for indicator,source,series,artifact,points in bundles:
            indicator.validate();source.validate();series.validate();artifact.validate()
            if indicator.code!=series.indicator or not points or len(points)>10000:raise RefreshError('Некорректный ответ ЦБ')
            seen=set()
            for point in points:
                point.validate(series)
                if point.period_start<start or point.period_end>end or point.period_start in seen:raise RefreshError('Некорректный ответ ЦБ')
                seen.add(point.period_start)
        if not bundles:raise RefreshError('В выбранном периоде ЦБ не вернул наблюдений')
        with self.session.write(token) as uow:
            ids=[]
            for bundle in bundles:ids.extend(uow.macro_data.ingest(*bundle))
            uow.commit()
        return len(ids)
