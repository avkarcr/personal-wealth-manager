"""Access lifecycle, presentation filters and export formatting retained for compatibility."""
import csv
import io
import os
from datetime import date, datetime

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
        return {
            **services["reports"].navigation(),
            "KIND_SHORT": calc.KIND_SHORT,
            "CATEGORIES": calc.CATEGORIES,
            "sign": CUR_SIGN,
            "KIND_LABELS": calc.KIND_LABELS,
            "FLOW_LABELS": calc.FLOW_LABELS,
            "ASSET_CLASSES": calc.ASSET_CLASSES,
            "today": date.today().isoformat(),
        }


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


    # ───────────────────────── аренда ─────────────────────────


    @app.post("/settings/general")
    def general_settings_save():
        language = request.form.get("language", "")
        try:
            services["change_language"].execute(language)
        except ValueError:
            abort(400)
        session["language"] = language
        return redirect(url_for("settings", _anchor="general"))


    PRIMARY_BOARDS = ["TQBR", "TQTF", "TQCB", "TQOB", "TQIR", "TQOD", "TQOY", "TQTD", "TQTE"]


    # ───────────────────────── курсы и настройки ─────────────────────────


    @app.get("/backup")
    def backup():
        vault.save(force=True)
        return send_file(vault.path, as_attachment=True, download_name=f"invest-{date.today().isoformat()}.enc")


    @app.get("/export.csv")
    def export_csv():
        holdings, flows = services["reports"].export()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Тип записи", "Счёт", "Дата", "Тип", "Название", "Тикер", "Класс", "Кол-во", "Ср. цена", "Цена", "Сумма", "Валюта", "Категория", "Источник", "Ожидается", "Комментарий"])
        for h in holdings:
            w.writerow(["Позиция", h["acc"], h["updated_at"], "", h["name"], h["ticker"], h["asset_class"], h["quantity"],
                        h["avg_price"], h["price"], h["quantity"] * h["price"], h["currency"], "", "", "", ""])
        for f in flows:
            w.writerow(["Операция", f["acc"], f["date"], calc.FLOW_LABELS[f["type"]], "", "", "", "", "", "", f["amount"],
                        f["currency"], f["category"], f["source"], "да" if f["pending"] else "", f["note"]])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=invest-{date.today().isoformat()}.csv"})

