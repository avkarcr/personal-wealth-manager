"""Капитал — локальный учёт инвестиций.

Запуск:  python app.py   →  http://127.0.0.1:5050
Все данные хранятся в зашифрованном файле invest.enc (AES-256-GCM, ключ из пароля через scrypt)
рядом с этим файлом и никуда не отправляются. Расшифрованная база существует только в памяти.
Исключения — только по нажатию кнопок: курсы с cbr.ru, цены с iss.moex.com
и цены криптовалют с публичных API бирж (Binance / Bybit / OKX) — передаются только тикеры.
"""
import csv
import io
import json
import os
import secrets
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from flask import (Flask, Response, abort, flash, redirect, render_template, request,
                   send_file, session, url_for)

import calc
from db import LEGACY_DB_PATH, VAULT_PATH, get_db, init_db, set_vault
from vault import Vault, WrongPassword, wipe_file

app = Flask(__name__)
app.secret_key = secrets.token_bytes(32)          # новый при каждом запуске → старые сессии недействительны
app.config.update(SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True)
VAULT = Vault(VAULT_PATH, idle_minutes=int(os.environ.get("INVEST_IDLE_MIN", 15)))
set_vault(VAULT)

OPEN_ENDPOINTS = {"static", "setup", "unlock"}


@app.before_request
def guard():
    if request.endpoint in OPEN_ENDPOINTS:
        return
    if VAULT.idle_expired():
        VAULT.lock()
        flash("Хранилище заблокировано из-за бездействия")
    if not VAULT.exists and not VAULT.unlocked:
        return redirect(url_for("setup"))
    if not VAULT.unlocked or session.get("vault") != VAULT.token:
        return redirect(url_for("unlock", next=request.full_path if request.method == "GET" else None))
    VAULT.touch()


@app.after_request
def persist(resp):
    if VAULT.unlocked and VAULT.dirty():
        VAULT.save()
    return resp


def _autolock_loop():
    while True:
        time.sleep(30)
        if VAULT.idle_expired():
            VAULT.lock()


threading.Thread(target=_autolock_loop, daemon=True).start()

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
    if not VAULT.unlocked:
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


def f_float(name, default=0.0):
    v = (request.form.get(name) or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(v) if v else default
    except ValueError:
        return default


def f_str(name, default=""):
    return (request.form.get(name) or default).strip()


def back(default="dashboard", **kw):
    return redirect(request.form.get("next") or request.referrer or url_for(default, **kw))


def snapshot():
    db = get_db()
    calc.take_snapshot(db, calc.Rates(db))


# ───────────────────────── доступ ─────────────────────────
def _safe_next():
    nxt = request.values.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if VAULT.exists:
        return redirect(url_for("unlock"))
    legacy = os.path.exists(LEGACY_DB_PATH)
    error = None
    if request.method == "POST":
        p1, p2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(p1) < 8:
            error = "Пароль должен быть не короче 8 символов"
        elif p1 != p2:
            error = "Пароли не совпадают"
        else:
            con = VAULT.create(p1, import_plain_path=LEGACY_DB_PATH if legacy else None)
            init_db(con)
            VAULT.save(force=True)
            if legacy:
                wipe_file(LEGACY_DB_PATH)
                for extra in (LEGACY_DB_PATH + ".backup", LEGACY_DB_PATH + "-journal"):
                    wipe_file(extra)
            session["vault"] = VAULT.token
            flash("Хранилище создано и зашифровано" + (" — старая база перенесена и удалена" if legacy else ""))
            return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="setup", legacy=legacy, error=error)


@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    if not VAULT.exists:
        return redirect(url_for("setup"))
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        try:
            if VAULT.unlocked:
                if not VAULT.check_password(pw):
                    raise WrongPassword()
            else:
                init_db(VAULT.unlock(pw))
                row = get_db().execute("SELECT value FROM settings WHERE key='idle_minutes'").fetchone()
                if row and row["value"].isdigit():
                    VAULT.idle_minutes = int(row["value"])
            session["vault"] = VAULT.token
            VAULT.touch()
            return redirect(_safe_next())
        except WrongPassword:
            time.sleep(1)
            error = "Неверный пароль"
    return render_template("auth.html", mode="unlock", error=error, next=request.values.get("next", ""))


@app.post("/lock")
def lock():
    VAULT.lock()
    session.clear()
    return redirect(url_for("unlock"))


@app.post("/settings/password")
def change_password():
    old, new, new2 = request.form.get("old", ""), request.form.get("new", ""), request.form.get("new2", "")
    if len(new) < 8 or new != new2:
        flash("Новый пароль: минимум 8 символов, оба поля должны совпадать")
    else:
        try:
            VAULT.change_password(old, new)
            session["vault"] = VAULT.token
            flash("Пароль изменён, файл перешифрован")
        except WrongPassword:
            flash("Текущий пароль неверный")
    return redirect(url_for("settings"))


@app.post("/settings/idle")
def set_idle():
    try:
        VAULT.idle_minutes = max(0, int(request.form.get("idle", 15)))
        get_db().execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('idle_minutes', ?)", (str(VAULT.idle_minutes),))
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
@app.post("/leases/save")
def lease_save():
    db = get_db()
    lid = request.form.get("id")
    freq = f_str("frequency", "month")
    if freq not in calc.FREQ_LABELS:
        abort(400)
    try:
        pay_day = max(0, min(31, int(f_str("pay_day") or 0)))
    except ValueError:
        pay_day = 0
    data = (int(request.form["account_id"]), f_str("tenant") or "Арендатор", f_str("contact"),
            f_str("start_date") or date.today().isoformat(), f_str("end_date"), f_float("rent_amount"),
            f_str("currency", "RUB"), freq, pay_day, f_float("deposit_amount"), f_str("deposit_currency", "RUB"),
            f_str("agent"), f_str("agent_fee"), f_str("utilities"), f_str("terms"))
    cols = "account_id, tenant, contact, start_date, end_date, rent_amount, currency, frequency, pay_day, deposit_amount, " \
           "deposit_currency, agent, agent_fee, utilities, terms"
    if lid:
        db.execute(f"UPDATE leases SET ({cols}) = ({','.join('?' * 15)}) WHERE id=?", data + (lid,))
    else:
        lid = db.execute(f"INSERT INTO leases({cols}) VALUES ({','.join('?' * 15)})", data).lastrowid
    lease = db.execute("SELECT * FROM leases WHERE id=?", (lid,)).fetchone()
    if lease["end_date"]:   # договор закончился раньше — убираем неоплаченные будущие периоды
        db.execute("DELETE FROM rent_payments WHERE lease_id=? AND status='expected' AND period_start>?",
                   (lid, lease["end_date"]))
        db.execute("UPDATE rent_payments SET period_end=? WHERE lease_id=? AND period_end>?",
                   (lease["end_date"], lid, lease["end_date"]))
    n = calc.generate_schedule(db, lease) if request.form.get("schedule") else 0
    db.commit()
    flash("Договор сохранён" + (f", добавлено платежей в график: {n}" if n else ""))
    return redirect(url_for("account", aid=lease["account_id"]) + "#rent")


@app.post("/leases/<int:lid>/schedule")
def lease_schedule(lid):
    db = get_db()
    lease = db.execute("SELECT * FROM leases WHERE id=?", (lid,)).fetchone() or abort(404)
    n = calc.generate_schedule(db, lease)
    db.commit()
    flash(f"Добавлено платежей в график: {n}" if n else "График уже актуален")
    return redirect(url_for("account", aid=lease["account_id"]) + "#rent")


@app.post("/leases/<int:lid>/delete")
def lease_delete(lid):
    db = get_db()
    lease = db.execute("SELECT * FROM leases WHERE id=?", (lid,)).fetchone() or abort(404)
    db.execute("DELETE FROM cashflows WHERE rent_payment_id IN (SELECT id FROM rent_payments WHERE lease_id=?)", (lid,))
    db.execute("DELETE FROM leases WHERE id=?", (lid,))
    db.commit()
    snapshot()
    flash("Договор и его платежи удалены")
    return redirect(url_for("account", aid=lease["account_id"]) + "#rent")


@app.post("/rent/save")
def rent_save():
    db = get_db()
    pid = request.form.get("id")
    lease = db.execute("SELECT * FROM leases WHERE id=?", (int(request.form["lease_id"]),)).fetchone() or abort(400)
    status = f_str("status", "expected")
    if status not in calc.RENT_STATUS:
        abort(400)
    ps = f_str("period_start") or date.today().isoformat()
    amount = f_float("amount") or lease["rent_amount"]
    currency = f_str("currency") or lease["currency"]
    rec_amount = f_float("received_amount")
    rec_cur = f_str("received_currency", "RUB")
    if status == "received" and not rec_amount:     # получил напрямую в валюте договора
        rec_amount, rec_cur = amount, currency
    data = (lease["id"], ps, f_str("period_end") or ps, f_str("due_date") or ps, amount, currency, status,
            f_str("tenant_paid_date"), f_str("received_date") if status == "received" else "",
            rec_amount if status == "received" else 0, rec_cur, f_float("cb_rate"), f_str("note"))
    cols = "lease_id, period_start, period_end, due_date, amount, currency, status, tenant_paid_date, received_date, " \
           "received_amount, received_currency, cb_rate, note"
    if pid:
        db.execute(f"UPDATE rent_payments SET ({cols}) = ({','.join('?' * 13)}) WHERE id=?", data + (pid,))
    else:
        pid = db.execute(f"INSERT INTO rent_payments({cols}) VALUES ({','.join('?' * 13)})", data).lastrowid
    if status == "received" and not f_str("received_date"):
        db.execute("UPDATE rent_payments SET received_date=? WHERE id=?", (date.today().isoformat(), pid))
    calc.sync_rent_cashflow(db, pid)
    db.commit()
    snapshot()
    return redirect(url_for("account", aid=lease["account_id"]) + "#rent")


@app.post("/rent/<int:pid>/delete")
def rent_delete(pid):
    db = get_db()
    row = db.execute("SELECT l.account_id FROM rent_payments p JOIN leases l ON l.id=p.lease_id WHERE p.id=?", (pid,)).fetchone() or abort(404)
    db.execute("DELETE FROM cashflows WHERE rent_payment_id=?", (pid,))
    db.execute("DELETE FROM rent_payments WHERE id=?", (pid,))
    db.commit()
    snapshot()
    return redirect(url_for("account", aid=row["account_id"]) + "#rent")


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
    return render_template("settings.html", rates=rates, archived=archived, db_path=VAULT_PATH, idle=VAULT.idle_minutes)


# ───────────────────────── счета ─────────────────────────
@app.post("/accounts/save")
def account_save():
    db = get_db()
    aid = request.form.get("id")
    kind = f_str("kind", "broker")
    data = (f_str("name") or "Без названия", kind, f_str("institution"), f_str("currency", "RUB"),
            f_float("current_value"), f_float("tax_rate"), f_str("note"))
    if kind not in calc.KIND_LABELS:
        abort(400)
    if aid:
        db.execute("UPDATE accounts SET name=?, kind=?, institution=?, currency=?, current_value=?, tax_rate=?, note=? WHERE id=?",
                   data + (aid,))
    else:
        cur = db.execute("INSERT INTO accounts(name, kind, institution, currency, current_value, tax_rate, note) "
                         "VALUES (?,?,?,?,?,?,?)", data)
        aid = cur.lastrowid
        # для недвижимости сразу фиксируем покупку как вложение
        price = f_float("purchase_price")
        if kind == "realty" and price > 0:
            db.execute("INSERT INTO cashflows(account_id, date, type, amount, currency, note) VALUES (?,?,?,?,?,?)",
                       (aid, f_str("purchase_date") or date.today().isoformat(), "deposit", price,
                        f_str("currency", "RUB"), "Покупка объекта"))
    db.commit()
    snapshot()
    return redirect(url_for("account", aid=aid))


@app.post("/accounts/<int:aid>/value")
def account_value(aid):
    db = get_db()
    db.execute("UPDATE accounts SET current_value=? WHERE id=?", (f_float("current_value"), aid))
    db.commit()
    snapshot()
    return redirect(url_for("account", aid=aid))


@app.post("/accounts/<int:aid>/archive")
def account_archive(aid):
    db = get_db()
    db.execute("UPDATE accounts SET archived = 1 - archived WHERE id=?", (aid,))
    db.commit()
    flash("Статус счёта изменён")
    return back()


@app.post("/accounts/<int:aid>/delete")
def account_delete(aid):
    db = get_db()
    db.execute("DELETE FROM accounts WHERE id=?", (aid,))
    db.commit()
    flash("Счёт удалён")
    return redirect(url_for("dashboard"))


@app.post("/accounts/<int:aid>/history")
def account_history_point(aid):
    """Ручная историческая оценка — чтобы график начинался не с сегодняшнего дня."""
    db = get_db()
    rates = calc.Rates(db)
    acc = db.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone() or abort(404)
    d = f_str("date") or date.today().isoformat()
    value_rub = rates.to_rub(f_float("value"), f_str("currency", acc["currency"]))
    inv = 0.0
    for f in db.execute("SELECT * FROM cashflows WHERE account_id=? AND date<=?", (aid, d)):
        if f["type"] == "deposit":
            inv += rates.to_rub(f["amount"], f["currency"])
        elif f["type"] == "withdrawal":
            inv -= rates.to_rub(f["amount"], f["currency"])
    db.execute("INSERT INTO snapshots(date, account_id, value_rub, invested_rub) VALUES (?,?,?,?) "
               "ON CONFLICT(date, account_id) DO UPDATE SET value_rub=excluded.value_rub, invested_rub=excluded.invested_rub",
               (d, aid, value_rub, inv or value_rub))
    db.commit()
    flash("Точка истории добавлена")
    return redirect(url_for("account", aid=aid))


# ───────────────────────── позиции ─────────────────────────
@app.post("/holdings/save")
def holding_save():
    db = get_db()
    hid = request.form.get("id")
    cls = f_str("asset_class", "Акции")
    qty = f_float("quantity")
    price = f_float("price")
    avg = f_float("avg_price") or price
    if cls == "Деньги":
        price = avg = 1.0
    data = (int(request.form["account_id"]), f_str("name") or f_str("ticker") or "Позиция", f_str("ticker").upper(),
            cls, f_str("currency", "RUB"), qty, avg, price, date.today().isoformat())
    if hid:
        db.execute("UPDATE holdings SET account_id=?, name=?, ticker=?, asset_class=?, currency=?, quantity=?, "
                   "avg_price=?, price=?, updated_at=? WHERE id=?", data + (hid,))
    else:
        db.execute("INSERT INTO holdings(account_id, name, ticker, asset_class, currency, quantity, avg_price, price, updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", data)
    db.commit()
    snapshot()
    return back()


@app.post("/holdings/<int:hid>/delete")
def holding_delete(hid):
    db = get_db()
    db.execute("DELETE FROM holdings WHERE id=?", (hid,))
    db.commit()
    snapshot()
    return back()


@app.post("/accounts/<int:aid>/prices")
def account_prices(aid):
    """Быстрое обновление цен сразу по всем позициям счёта."""
    db = get_db()
    for key, val in request.form.items():
        if key.startswith("price_"):
            try:
                p = float(val.replace(",", ".").replace(" ", "").replace(" ", ""))
            except ValueError:
                continue
            db.execute("UPDATE holdings SET price=?, updated_at=? WHERE id=? AND account_id=?",
                       (p, date.today().isoformat(), int(key[6:]), aid))
    db.commit()
    snapshot()
    flash("Цены обновлены")
    return redirect(url_for("account", aid=aid))


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
    for h in db.execute("SELECT * FROM holdings WHERE account_id=? AND ticker<>'' AND asset_class<>'Деньги'", (aid,)).fetchall():
        p = crypto_usd(h["ticker"])
        if p is not None:
            price = p * usd / rates.r.get(h["currency"], usd)
            db.execute("UPDATE holdings SET price=?, updated_at=? WHERE id=?", (price, date.today().isoformat(), h["id"]))
            ok += 1
        else:
            fail.append(h["ticker"])
    db.commit()
    snapshot()
    flash(f"Обновлено цен: {ok}" + (f". Не найдены на биржах: {', '.join(fail)}" if fail else ""))
    return redirect(url_for("account", aid=aid))


@app.post("/accounts/<int:aid>/moex")
def account_moex(aid):
    db = get_db()
    ok, fail = 0, []
    for h in db.execute("SELECT * FROM holdings WHERE account_id=? AND ticker<>'' AND asset_class<>'Деньги'", (aid,)).fetchall():
        try:
            p = moex_price(h["ticker"])
        except Exception:
            p = None
        if p:
            db.execute("UPDATE holdings SET price=?, updated_at=? WHERE id=?", (p, date.today().isoformat(), h["id"]))
            ok += 1
        else:
            fail.append(h["ticker"])
    db.commit()
    snapshot()
    flash(f"Обновлено цен: {ok}" + (f". Не найдены на MOEX: {', '.join(fail)}" if fail else ""))
    return redirect(url_for("account", aid=aid))


# ───────────────────────── операции ─────────────────────────
@app.post("/flows/save")
def flow_save():
    db = get_db()
    fid = request.form.get("id")
    if fid and db.execute("SELECT rent_payment_id FROM cashflows WHERE id=?", (fid,)).fetchone()["rent_payment_id"]:
        flash("Эта операция создана из платежа по аренде — измените сам платёж на странице объекта")
        return back()
    ftype = f_str("type", "deposit")
    if ftype not in calc.FLOW_LABELS:
        abort(400)
    pending = 1 if (request.form.get("pending") and ftype == "income") else 0
    data = (int(request.form["account_id"]), f_str("date") or date.today().isoformat(), ftype,
            abs(f_float("amount")), f_str("currency", "RUB").upper(), f_str("category"), f_str("source"), pending, f_str("note"))
    if fid:
        db.execute("UPDATE cashflows SET account_id=?, date=?, type=?, amount=?, currency=?, category=?, source=?, pending=?, note=? "
                   "WHERE id=?", data + (fid,))
    else:
        db.execute("INSERT INTO cashflows(account_id, date, type, amount, currency, category, source, pending, note) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", data)
    db.commit()
    snapshot()
    return back()


@app.post("/flows/<int:fid>/paid")
def flow_paid(fid):
    db = get_db()
    row = db.execute("SELECT c.rent_payment_id, l.account_id FROM cashflows c LEFT JOIN rent_payments p ON p.id=c.rent_payment_id "
                     "LEFT JOIN leases l ON l.id=p.lease_id WHERE c.id=?", (fid,)).fetchone()
    if row and row["rent_payment_id"]:
        flash("Отметьте получение денег от риелтора в платеже — там же укажите сумму и курс")
        return redirect(url_for("account", aid=row["account_id"]) + "#rent")
    db.execute("UPDATE cashflows SET pending=0, date=? WHERE id=?", (date.today().isoformat(), fid))
    db.commit()
    flash("Отмечено как полученное")
    return back()


@app.post("/flows/<int:fid>/delete")
def flow_delete(fid):
    db = get_db()
    row = db.execute("SELECT rent_payment_id FROM cashflows WHERE id=?", (fid,)).fetchone()
    if row and row["rent_payment_id"]:
        flash("Эта операция создана из платежа по аренде — удалите сам платёж на странице объекта")
        return back()
    db.execute("DELETE FROM cashflows WHERE id=?", (fid,))
    db.commit()
    snapshot()
    return back()


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
    VAULT.save(force=True)
    return send_file(VAULT_PATH, as_attachment=True, download_name=f"invest-{date.today().isoformat()}.enc")


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


if __name__ == "__main__":
    import webbrowser, threading
    port = int(os.environ.get("PORT", 5050))
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"\n  Капитал запущен: http://127.0.0.1:{port}\n  Остановить: Ctrl+C\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=False)
