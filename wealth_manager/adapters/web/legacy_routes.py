"""Legacy read models, settings and market integrations awaiting stage 3."""
from .forms import f_str, f_float, back
import csv
import io
import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from flask import (Response, abort, flash, redirect, render_template, request,
                   send_file, session, url_for, g)

import calc
from db import init_db
from vault import WrongPassword, wipe_file


def register_routes(app, runtime):
    vault = runtime.vault
    services = runtime.services
    legacy_db_path = app.config["LEGACY_DB_PATH"]
    get_db = runtime.get_connection

    FIAT = {"RUB", "USD", "EUR", "CNY", "GBP", "CHF", "JPY", "KZT", "AED", "TRY", "BYN", "UAH", "AMD", "GEL", "UZS",
            "KGS", "AZN", "HKD", "SGD", "THB", "INR", "CAD", "AUD", "SEK", "NOK", "PLN", "CZK", "RSD", "VND", "IDR"}
    CUR_SIGN = {"RUB": "₽", "USD": "$", "EUR": "€", "CNY": "¥", "GBP": "£", "CHF": "₣", "KZT": "₸", "AED": "AED", "TRY": "₺"}


    # ───────────────────────── форматирование ─────────────────────────
    def _fmt(x, dec):
        s = f"{abs(x):,.{dec}f}".replace(",", " ").replace(".", ",")
        return ("−" if x < 0 else "") + s


    @app.template_filter("money")
    def money(x, cur=None, dec=0):
        if x is None:
            return "—"
        if x and x != int(x) and (abs(x) < 1 or (dec == 0 and abs(x) < 10)):
            s = f"{abs(x):.6f}".rstrip("0").rstrip(".").replace(".", ",")
            s = ("−" if x < 0 else "") + s
        else:
            s = _fmt(x, dec)
        return f"{s} {CUR_SIGN.get(cur, cur)}" if cur else s


    @app.template_filter("num")
    def num(x, dec=2):
        if x is None:
            return "—"
        s = f"{x:,.{dec}f}".rstrip("0").rstrip(".") if dec else f"{x:,.0f}"
        return s.replace(",", " ").replace(".", ",") if s else "0"


    @app.template_filter("pct")
    def pct(x, sign=True):
        if x is None:
            return "—"
        s = f"{x * 100:+.1f}" if sign else f"{x * 100:.1f}"
        return s.replace(".", ",").replace("-", "−") + " %"


    @app.template_filter("signed")
    def signed(x, cur=None):
        if x is None:
            return "—"
        return ("+" if x > 0 else "") + money(x, cur)


    @app.template_filter("rudate")
    def rudate(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            return s or ""


    @app.context_processor
    def inject():
        if not vault.unlocked:
            return {"locked": True}
        db = get_db()
        rates = calc.Rates(db)
        nav = db.execute("SELECT id, name, kind FROM accounts WHERE archived=0 ORDER BY "
                         "CASE kind WHEN 'broker' THEN 0 WHEN 'crypto' THEN 1 WHEN 'realty' THEN 2 WHEN 'other' THEN 3 ELSE 4 END, name").fetchall()
        return {
            "base": rates.base,
            "KIND_SHORT": calc.KIND_SHORT,
            "CATEGORIES": calc.CATEGORIES,
            "missing_rates": calc.missing_rates(db, rates),
            "nav_invest": [a for a in nav if a["kind"] != "freelance"],
            "nav_work": [a for a in nav if a["kind"] == "freelance"],
            "sign": CUR_SIGN,
            "currencies": list(rates.r.keys()),
            "KIND_LABELS": calc.KIND_LABELS,
            "FLOW_LABELS": calc.FLOW_LABELS,
            "ASSET_CLASSES": calc.ASSET_CLASSES,
            "today": date.today().isoformat(),
            "nav_accounts": nav,
        }


    def snapshot():
        db = get_db()
        calc.take_snapshot(db, calc.Rates(db))


    # ───────────────────────── доступ ─────────────────────────
    def _safe_next():
        nxt = request.values.get("next") or ""
        return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard")


    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if vault.exists:
            return redirect(url_for("unlock"))
        legacy = os.path.exists(legacy_db_path)
        error = None
        if request.method == "POST":
            p1, p2 = request.form.get("password", ""), request.form.get("password2", "")
            if len(p1) < 8:
                error = "Пароль должен быть не короче 8 символов"
            elif p1 != p2:
                error = "Пароли не совпадают"
            else:
                con = vault.create(p1, import_plain_path=legacy_db_path if legacy else None)
                init_db(con)
                vault.save(force=True)
                if legacy:
                    wipe_file(legacy_db_path)
                    for extra in (legacy_db_path + ".backup", legacy_db_path + "-journal"):
                        wipe_file(extra)
                session["vault"] = vault.token
                flash("Хранилище создано и зашифровано" + (" — старая база перенесена и удалена" if legacy else ""))
                return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="setup", legacy=legacy, error=error)


    @app.route("/unlock", methods=["GET", "POST"])
    def unlock():
        if not vault.exists:
            return redirect(url_for("setup"))
        error = None
        if request.method == "POST":
            pw = request.form.get("password", "")
            try:
                if vault.unlocked:
                    if not vault.check_password(pw):
                        raise WrongPassword()
                else:
                    init_db(vault.unlock(pw))
                    row = get_db().execute("SELECT value FROM settings WHERE key='idle_minutes'").fetchone()
                    if row and row["value"].isdigit():
                        vault.idle_minutes = int(row["value"])
                session["vault"] = vault.token
                vault.touch()
                return redirect(_safe_next())
            except WrongPassword:
                time.sleep(1)
                error = "Неверный пароль"
        return render_template("auth.html", mode="unlock", error=error, next=request.values.get("next", ""))


    @app.post("/lock")
    def lock():
        vault.lock()
        language = g.language
        session.clear()
        session["language"] = language
        return redirect(url_for("unlock"))


    @app.post("/settings/password")
    def change_password():
        old, new, new2 = request.form.get("old", ""), request.form.get("new", ""), request.form.get("new2", "")
        if len(new) < 8 or new != new2:
            flash("Новый пароль: минимум 8 символов, оба поля должны совпадать")
        else:
            try:
                vault.change_password(old, new)
                session["vault"] = vault.token
                flash("Пароль изменён, файл перешифрован")
            except WrongPassword:
                flash("Текущий пароль неверный")
        return redirect(url_for("settings"))


    @app.post("/settings/idle")
    def set_idle():
        try:
            vault.idle_minutes = max(0, int(request.form.get("idle", 15)))
            get_db().execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('idle_minutes', ?)", (str(vault.idle_minutes),))
            get_db().commit()
            flash("Сохранено")
        except ValueError:
            pass
        return redirect(url_for("settings"))


    # ───────────────────────── страницы ─────────────────────────
    @app.route("/")
    def dashboard():
        db = get_db()
        rates = calc.Rates(db)
        calc.take_snapshot(db, rates)
        items, total, by_class, by_cur = calc.portfolio(db, rates)
        hist = calc.history(db, rates)
        inc = calc.income_report(db, rates)
        has_any = db.execute("SELECT 1 FROM accounts WHERE archived=0 LIMIT 1").fetchone() is not None
        recent = db.execute(
            "SELECT c.*, a.name AS acc_name FROM cashflows c JOIN accounts a ON a.id=c.account_id "
            "ORDER BY c.date DESC, c.id DESC LIMIT 6").fetchall()
        return render_template(
            "dashboard.html", items=items, total=total,
            by_class=sorted(by_class.items(), key=lambda x: -x[1]),
            by_cur=sorted(by_cur.items(), key=lambda x: -x[1]),
            hist=hist, recent=recent, inc=inc, has_any=has_any)


    @app.route("/accounts/<int:aid>")
    def account(aid):
        db = get_db()
        acc = db.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone() or abort(404)
        rates = calc.Rates(db)
        if acc["kind"] == "freelance":
            flows = db.execute("SELECT * FROM cashflows WHERE account_id=? ORDER BY pending DESC, date DESC, id DESC", (aid,)).fetchall()
            inc = calc.income_report(db, rates, account_id=aid)
            tax_est = inc["totals"].get("freelance", 0) * (acc["tax_rate"] or 0) / 100
            return render_template("freelance.html", acc=acc, flows=flows, inc=inc, tax_est=tax_est)
        holdings = db.execute("SELECT * FROM holdings WHERE account_id=? ORDER BY asset_class, name", (aid,)).fetchall()
        flows = db.execute("SELECT * FROM cashflows WHERE account_id=? ORDER BY date DESC, id DESC", (aid,)).fetchall()
        rent = None
        if acc["kind"] == "realty":
            rent = realty_context(db, acc, rates)
        m = calc.account_metrics(db, acc, rates, holdings, flows)
        rows = []
        for h in holdings:
            val = rates.to_base(h["quantity"] * h["price"], h["currency"])
            pl = (h["price"] - h["avg_price"]) * h["quantity"]
            rows.append({"h": h, "value": val, "share": val / m["value"] if m["value"] else 0,
                         "pl": pl, "pl_pct": (h["price"] / h["avg_price"] - 1) if h["avg_price"] else None})
        hist = calc.history(db, rates, aid)
        return render_template("account.html", acc=acc, m=m, rows=rows, flows=flows, hist=hist, rent=rent,
                               by_class=sorted(m["by_class"].items(), key=lambda x: -x[1]))


    def realty_context(db, acc, rates):
        leases = db.execute("SELECT * FROM leases WHERE account_id=? ORDER BY start_date DESC", (acc["id"],)).fetchall()
        pays = db.execute("SELECT p.*, l.tenant, l.agent FROM rent_payments p JOIN leases l ON l.id=p.lease_id "
                          "WHERE l.account_id=? ORDER BY p.period_start DESC, p.id DESC", (acc["id"],)).fetchall()
        pays = [calc.payment_view(p, rates) for p in pays]
        year_ago = (date.today() - timedelta(days=365)).isoformat()
        summary = {
            "overdue": sum(p["amount_base"] for p in pays if p["overdue"]),
            "overdue_n": sum(1 for p in pays if p["overdue"]),
            "agent": sum(p["amount_base"] for p in pays if p["status"] == "agent"),
            "agent_n": sum(1 for p in pays if p["status"] == "agent"),
            "fx_diff": sum(p["fx_diff"] or 0 for p in pays if (p["received_date"] or "") >= year_ago),
            "has_fx": any(p["fx_diff"] is not None for p in pays),
        }
        lease_info = []
        today = date.today().isoformat()
        for l in leases:
            st = "active" if l["start_date"] <= today and (not l["end_date"] or l["end_date"] >= today) else (
                 "future" if l["start_date"] > today else "ended")
            lease_info.append({"l": l, "status": st,
                               "monthly": l["rent_amount"] / calc.FREQ_MONTHS[l["frequency"]] if l["frequency"] in calc.FREQ_MONTHS else None})
        return {"leases": lease_info, "payments": pays, "summary": summary, "occ": calc.occupancy(db, acc),
                "FREQ": calc.FREQ_LABELS, "STATUS": calc.RENT_STATUS}


    # ───────────────────────── аренда ─────────────────────────


    @app.get("/api/cbr")
    def api_cbr():
        """Курс ЦБ РФ на дату (₽ за 1 единицу) — запрос к cbr.ru только по нажатию кнопки."""
        cur = (request.args.get("cur") or "USD").upper()
        try:
            d = datetime.strptime(request.args.get("date") or date.today().isoformat(), "%Y-%m-%d")
            with urllib.request.urlopen(f"https://www.cbr.ru/scripts/XML_daily.asp?date_req={d.strftime('%d/%m/%Y')}", timeout=10) as r:
                root = ET.fromstring(r.read())
            for v in root.findall("Valute"):
                if v.findtext("CharCode") == cur:
                    rate = float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal").replace(",", "."))
                    return {"rate": round(rate, 4), "date": root.get("Date")}
            return {"error": f"У ЦБ нет курса {cur}"}, 404
        except Exception as e:
            return {"error": f"Не удалось получить курс ЦБ: {e}"}, 502


    @app.route("/operations")
    def operations():
        db = get_db()
        q = "SELECT c.*, a.name AS acc_name FROM cashflows c JOIN accounts a ON a.id=c.account_id WHERE 1=1"
        args = []
        acc_f, type_f, year_f = request.args.get("account"), request.args.get("type"), request.args.get("year")
        if acc_f:
            q += " AND c.account_id=?"; args.append(acc_f)
        if type_f:
            q += " AND c.type=?"; args.append(type_f)
        if year_f:
            q += " AND substr(c.date,1,4)=?"; args.append(year_f)
        flows = db.execute(q + " ORDER BY c.date DESC, c.id DESC", args).fetchall()
        rates = calc.Rates(db)
        totals = {k: 0.0 for k in calc.FLOW_LABELS}
        for f in flows:
            if not f["pending"]:
                totals[f["type"]] += rates.to_base(f["amount"], f["currency"])
        years = [r[0] for r in db.execute("SELECT DISTINCT substr(date,1,4) FROM cashflows ORDER BY 1 DESC")]
        # доход по месяцам за последние 12 мес. — для мини-графика
        monthly = {}
        for r in db.execute("SELECT substr(date,1,7) AS m, amount, currency FROM cashflows WHERE type='income' "
                            "AND pending=0 AND date >= date('now','-12 months','start of month')"):
            monthly[r["m"]] = monthly.get(r["m"], 0) + rates.to_base(r["amount"], r["currency"])
        return render_template("operations.html", flows=flows, totals=totals, years=years,
                               f={"account": acc_f, "type": type_f, "year": year_f},
                               monthly=sorted(monthly.items()))


    @app.route("/income")
    def income():
        db = get_db()
        rates = calc.Rates(db)
        year = request.args.get("year") or None
        inc = calc.income_report(db, rates, year=year)
        years = [r[0] for r in db.execute("SELECT DISTINCT substr(date,1,4) FROM cashflows WHERE type='income' ORDER BY 1 DESC")]
        return render_template("income.html", inc=inc, years=years, year=year)


    @app.route("/settings")
    def settings():
        db = get_db()
        rates = db.execute("SELECT * FROM rates ORDER BY currency='RUB' DESC, currency").fetchall()
        archived = db.execute("SELECT * FROM accounts WHERE archived=1 ORDER BY name").fetchall()
        return render_template("settings.html", rates=rates, archived=archived, db_path=vault.path, idle=vault.idle_minutes)


    @app.post("/settings/general")
    def general_settings_save():
        language = request.form.get("language", "")
        try:
            services["change_language"].execute(language)
        except ValueError:
            abort(400)
        session["language"] = language
        return redirect(url_for("settings", _anchor="general"))


    def _iss_rows(block):
        cols = block.get("columns", [])
        return [dict(zip(cols, r)) for r in block.get("data", [])]


    PRIMARY_BOARDS = ["TQBR", "TQTF", "TQCB", "TQOB", "TQIR", "TQOD", "TQOY", "TQTD", "TQTE"]


    def moex_price(ticker):
        for market in ("shares", "bonds"):
            url = (f"https://iss.moex.com/iss/engine/stock/markets/{market}/securities/{ticker}.json"
                   "?iss.meta=off&iss.only=securities,marketdata")
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.load(r)
            secs = {s["BOARDID"]: s for s in _iss_rows(data.get("securities", {}))}
            mds = _iss_rows(data.get("marketdata", {}))
            if not secs:
                continue
            mds.sort(key=lambda m: PRIMARY_BOARDS.index(m["BOARDID"]) if m.get("BOARDID") in PRIMARY_BOARDS else 99)
            for md in mds:
                sec = secs.get(md.get("BOARDID"), {})
                p = md.get("LAST") or md.get("MARKETPRICE") or md.get("LCURRENTPRICE") or sec.get("PREVPRICE")
                if p:
                    if market == "bonds":
                        p = p * (sec.get("FACEVALUE") or 1000) / 100
                    return float(p)
        return None


    def _get_json(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (local invest app)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)


    def crypto_usd(symbol):
        """Цена монеты в USDT (≈ USD) с публичных API бирж; только по тикеру, без ключей."""
        s = symbol.upper().strip()
        if s in calc.STABLECOINS:
            return 1.0
        sources = (
            (f"https://api.binance.com/api/v3/ticker/price?symbol={s}USDT", lambda d: d.get("price")),
            (f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={s}USDT",
             lambda d: (d.get("result", {}).get("list") or [{}])[0].get("lastPrice")),
            (f"https://www.okx.com/api/v5/market/ticker?instId={s}-USDT", lambda d: (d.get("data") or [{}])[0].get("last")),
        )
        for url, pick in sources:
            try:
                p = pick(_get_json(url))
                if p and float(p) > 0:
                    return float(p)
            except Exception:
                continue
        return None


    @app.post("/accounts/<int:aid>/crypto")
    def account_crypto(aid):
        db = get_db()
        rates = calc.Rates(db)
        usd = rates.r.get("USD")
        if not usd:
            flash("Добавьте курс USD в «Курсах и данных» — цены монет приходят в долларах")
            return redirect(url_for("account", aid=aid))
        ok, fail = 0, []
        prices = {}
        for h in db.execute("SELECT * FROM holdings WHERE account_id=? AND ticker<>'' AND asset_class<>'Деньги'", (aid,)).fetchall():
            p = crypto_usd(h["ticker"])
            if p is not None:
                price = p * usd / rates.r.get(h["currency"], usd)
                prices[h["id"]] = price
                ok += 1
            else:
                fail.append(h["ticker"])
        services["positions"].update_prices(aid, prices)
        flash(f"Обновлено цен: {ok}" + (f". Не найдены на биржах: {', '.join(fail)}" if fail else ""))
        return redirect(url_for("account", aid=aid))


    @app.post("/accounts/<int:aid>/moex")
    def account_moex(aid):
        db = get_db()
        ok, fail = 0, []
        prices = {}
        for h in db.execute("SELECT * FROM holdings WHERE account_id=? AND ticker<>'' AND asset_class<>'Деньги'", (aid,)).fetchall():
            try:
                p = moex_price(h["ticker"])
            except Exception:
                p = None
            if p:
                prices[h["id"]] = p
                ok += 1
            else:
                fail.append(h["ticker"])
        services["positions"].update_prices(aid, prices)
        flash(f"Обновлено цен: {ok}" + (f". Не найдены на MOEX: {', '.join(fail)}" if fail else ""))
        return redirect(url_for("account", aid=aid))


    # ───────────────────────── курсы и настройки ─────────────────────────
    @app.post("/rates/save")
    def rates_save():
        db = get_db()
        for key, val in request.form.items():
            if key.startswith("rate_"):
                try:
                    r = float(val.replace(",", ".").replace(" ", ""))
                except ValueError:
                    continue
                if r > 0:
                    db.execute("UPDATE rates SET rate=?, updated_at=? WHERE currency=?", (r, date.today().isoformat(), key[5:]))
        new = f_str("new_currency").upper()
        if new and f_float("new_rate") > 0:
            db.execute("INSERT OR REPLACE INTO rates(currency, rate, updated_at) VALUES (?,?,?)",
                       (new, f_float("new_rate"), date.today().isoformat()))
        base = f_str("base_currency")
        if base:
            db.execute("UPDATE settings SET value=? WHERE key='base_currency'", (base,))
        db.execute("UPDATE rates SET rate=1 WHERE currency='RUB'")
        db.commit()
        snapshot()
        flash("Сохранено")
        return redirect(url_for("settings"))


    @app.post("/rates/<cur>/delete")
    def rate_delete(cur):
        db = get_db()
        if cur != "RUB":
            db.execute("DELETE FROM rates WHERE currency=?", (cur,))
            db.execute("UPDATE settings SET value='RUB' WHERE key='base_currency' AND value=?", (cur,))
            db.commit()
        return redirect(url_for("settings"))


    @app.post("/rates/cbr")
    def rates_cbr():
        db = get_db()
        try:
            with urllib.request.urlopen("https://www.cbr.ru/scripts/XML_daily.asp", timeout=10) as r:
                root = ET.fromstring(r.read())
            cbr = {}
            for v in root.findall("Valute"):
                code = v.findtext("CharCode")
                nominal = float(v.findtext("Nominal").replace(",", "."))
                value = float(v.findtext("Value").replace(",", "."))
                cbr[code] = value / nominal
            n = 0
            for row in db.execute("SELECT currency FROM rates WHERE currency<>'RUB'").fetchall():
                if row["currency"] in cbr:
                    db.execute("UPDATE rates SET rate=?, updated_at=? WHERE currency=?",
                               (round(cbr[row["currency"]], 4), date.today().isoformat(), row["currency"]))
                    n += 1
            db.commit()
            snapshot()
            flash(f"Курсы ЦБ на {root.get('Date')} загружены ({n} валют)")
        except Exception as e:
            flash(f"Не удалось получить курсы ЦБ: {e}")
        return redirect(url_for("settings"))


    @app.post("/rates/crypto")
    def rates_crypto():
        """Курсы криптовалют к рублю (через USD) — для доходов, полученных в монетах."""
        db = get_db()
        rates = calc.Rates(db)
        usd = rates.r.get("USD")
        if not usd:
            flash("Сначала задайте курс USD")
            return redirect(url_for("settings"))
        n, fail = 0, []
        for row in db.execute("SELECT currency FROM rates").fetchall():
            c = row["currency"]
            if c in FIAT:
                continue
            p = crypto_usd(c)
            if p:
                db.execute("UPDATE rates SET rate=?, updated_at=? WHERE currency=?", (round(p * usd, 6), date.today().isoformat(), c))
                n += 1
            else:
                fail.append(c)
        db.commit()
        snapshot()
        flash(f"Обновлено криптокурсов: {n}" + (f". Не найдены: {', '.join(fail)}" if fail else "")
              if n or fail else "Нет криптовалют в списке курсов — добавьте, например, BTC, ETH, USDT")
        return redirect(url_for("settings"))


    @app.get("/backup")
    def backup():
        vault.save(force=True)
        return send_file(vault.path, as_attachment=True, download_name=f"invest-{date.today().isoformat()}.enc")


    @app.get("/export.csv")
    def export_csv():
        db = get_db()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Тип записи", "Счёт", "Дата", "Тип", "Название", "Тикер", "Класс", "Кол-во", "Ср. цена", "Цена", "Сумма", "Валюта", "Категория", "Источник", "Ожидается", "Комментарий"])
        for h in db.execute("SELECT h.*, a.name AS acc FROM holdings h JOIN accounts a ON a.id=h.account_id"):
            w.writerow(["Позиция", h["acc"], h["updated_at"], "", h["name"], h["ticker"], h["asset_class"], h["quantity"],
                        h["avg_price"], h["price"], h["quantity"] * h["price"], h["currency"], "", "", "", ""])
        for f in db.execute("SELECT c.*, a.name AS acc FROM cashflows c JOIN accounts a ON a.id=c.account_id ORDER BY date"):
            w.writerow(["Операция", f["acc"], f["date"], calc.FLOW_LABELS[f["type"]], "", "", "", "", "", "", f["amount"],
                        f["currency"], f["category"], f["source"], "да" if f["pending"] else "", f["note"]])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=invest-{date.today().isoformat()}.csv"})

