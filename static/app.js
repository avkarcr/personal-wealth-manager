/* Капитал — интерфейс: диалоги, мелкие удобства и графики на чистом SVG (без внешних библиотек). */
(function () {
  const t = text => (window.UI_MESSAGES || {})[text] || text;
  const locale = document.documentElement.lang === 'en' ? 'en-US' : 'ru-RU';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  /* ── тема ── */
  const themeBtn = $('#theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {}
    window.dispatchEvent(new Event('resize'));
  });

  /* ── диалоги ── */
  const FLOW_HINTS = {
    deposit: t('Ваши деньги, внесённые на счёт (или покупка/доплата за объект).'),
    withdrawal: t('Деньги, которые вы забрали со счёта себе.'),
    income: t('Дивиденды, купоны, аренда, стейкинг, оплата за проект.'),
    expense: t('Комиссии, налоги, газ, расходы на содержание объекта.'),
  };
  const INCOME_HINT_BY_KIND = {
    broker: t('Для статистики доходов: сами деньги уже учтены в позиции «Деньги» на счёте.'),
    crypto: t('Для статистики доходов: монеты от стейкинга/фарминга добавьте и в количество позиции. Можно указать сумму прямо в монетах (ETH, USDT…).'),
    realty: t('Аренда входит в прибыль объекта.'),
    freelance: t('Оплата за работу. Если деньги ещё не пришли — отметьте «ожидается».'),
    other: '',
  };
  const RENT_HINTS = {
    expected: t('Арендатор ещё не заплатил.'),
    agent: t('Арендатор заплатил риелтору, но до вас деньги ещё не дошли. Сумма будет видна как «у риелтора».'),
    received: t('Укажите, сколько вы фактически получили и в какой валюте — курс посчитается сам.'),
  };
  const numv = v => parseFloat(String(v || '').replace(/\s/g, '').replace(',', '.')) || 0;
  const fmt2 = v => v.toLocaleString(locale, { maximumFractionDigits: 2 });
  function fxCalc(dlg) {
    const out = $('[data-fx-out]', dlg);
    if (!out) return;
    const f = $('form', dlg);
    const amt = numv(f.amount.value), rec = numv(f.received_amount.value), cb = numv(f.cb_rate.value);
    const cur = f.currency.value, rcur = f.received_currency.value;
    if (!amt || !rec || cur === rcur) { out.innerHTML = cur === rcur ? '' : '<span class="muted">' + t('Введите полученную сумму — посчитаю курс') + '</span>'; return; }
    const eff = rec / amt;
    let html = `${t('Курс:')} <b>1 ${cur} = ${fmt2(eff)} ${rcur}</b>`;
    if (cb && rcur === 'RUB') {
      const diff = rec - amt * cb;
      html += `<br>${t('По ЦБ было бы')} ${fmt2(amt * cb)} ₽ → <b class="${diff < 0 ? 'neg' : 'pos'}">${diff < 0 ? '−' : '+'}${fmt2(Math.abs(diff))} ₽</b> (${fmt2((eff / cb - 1) * 100)}%)`;
    }
    out.innerHTML = html;
  }
  document.addEventListener('input', e => { const d = e.target.closest('dialog'); if (d && $('[data-fx-out]', d)) fxCalc(d); });
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-cbr]');
    if (!b) return;
    const f = b.closest('form');
    const d = f.received_date.value || f.tenant_paid_date.value || f.due_date.value;
    b.disabled = true; const previousText = b.textContent; b.textContent = '…';
    try {
      const r = await fetch(`/api/cbr?cur=${encodeURIComponent(f.currency.value)}&date=${d}`);
      const j = await r.json();
      if (j.rate) { f.cb_rate.value = String(j.rate).replace('.', ','); fxCalc(b.closest('dialog')); }
      else alert(j.error || t('Нет курса'));
    } catch (err) { alert(t('Не удалось связаться с cbr.ru')); }
    b.disabled = false; b.textContent = previousText;
  });
  const KIND_HINTS = {
    broker: t('Брокерский счёт или ИИС: позиции, пополнения, дивиденды и купоны.'),
    crypto: t('Биржа или кошелёк: монеты, стейблкоины, доход от стейкинга, фарминга, аирдропов, P2P.'),
    realty: t('Объект недвижимости: оценка, аренда и расходы.'),
    freelance: t('Площадка или постоянный клиент: оплаты по проектам, комиссии, налог. Не входит в капитал — только в доходы.'),
    other: t('Всё остальное: металлы, вклад, доля в бизнесе…'),
  };
  const INST_PH = { broker: t('Т-Инвестиции, БКС…'), crypto: 'Bybit, OKX, Ledger…', realty: t('Казань, ул. Ленина 5'), freelance: t('Kwork, FL.ru, Upwork, напрямую'), other: '' };
  const NAME_PH = { broker: t('ИИС, Брокерский счёт…'), crypto: t('Bybit, Холодный кошелёк…'), realty: t('Квартира на Ленина'), freelance: t('Kwork или Прямые клиенты'), other: t('Золото, вклад…') };
  const INST_LABEL = { broker: t('Брокер'), crypto: t('Биржа / кошелёк'), realty: t('Город / адрес'), freelance: t('Площадка'), other: t('Где хранится') };
  const SOURCE_LABEL = { broker: t('Бумага'), crypto: t('Монета / протокол'), realty: t('Арендатор'), freelance: t('Клиент / проект'), other: t('Источник') };
  const SOURCE_PH = { broker: 'SBER', crypto: 'ETH, Lido, Bybit Earn', realty: '', freelance: t('ООО Ромашка / лендинг'), other: '' };

  function syncDialog(dlg) {
    const form = $('form', dlg);
    const kind = form.querySelector('[name=kind]:checked');
    if (kind) {
      dlg.dataset.kind = kind.value;
      const lbl = $('[data-kind-label]', dlg);
      if (lbl) lbl.textContent = INST_LABEL[kind.value] || t('Где');
      const ii = form.querySelector('[name=institution]'); if (ii) ii.placeholder = INST_PH[kind.value] || '';
      const ni = form.querySelector('[name=name]'); if (ni) ni.placeholder = NAME_PH[kind.value] || '';
      const kh = $('[data-kind-hint]', dlg);
      if (kh) kh.textContent = KIND_HINTS[kind.value] || '';
    }
    const cls = form.querySelector('[name=asset_class]');
    if (cls) {
      dlg.classList.toggle('cash', cls.value === 'Деньги');
      const q = $('[data-qty-label]', dlg);
      if (q) q.textContent = cls.value === 'Деньги' ? t('Сумма') : t('Количество');
    }
    const rs = form.querySelector('[name=status]:checked');
    if (rs) {
      dlg.dataset.status = rs.value;
      const h = $('[data-rent-hint]', dlg);
      if (h) h.textContent = RENT_HINTS[rs.value] || '';
      const rc = form.querySelector('[name=received_currency]'), cc = form.querySelector('[name=currency]');
      dlg.classList.toggle('same-cur', !!(rc && cc && rc.value === cc.value));
      fxCalc(dlg);
    }
    const accSel = form.querySelector('select[name=account_id]');
    if (accSel && form.querySelector('[name=type]')) {
      const opt = accSel.selectedOptions[0];
      const ak = (opt && opt.dataset.kind) || 'other';
      dlg.dataset.acckind = ak;
      let ft = form.querySelector('[name=type]:checked');
      if (ak === 'freelance' && ft && (ft.value === 'deposit' || ft.value === 'withdrawal')) {
        form.querySelector('[name=type][value=income]').checked = true;
        ft = form.querySelector('[name=type]:checked');
      }
      dlg.dataset.ftype = ft ? ft.value : '';
      const hint = $('[data-flow-hint]', dlg);
      if (hint) hint.textContent = (ft && ft.value === 'income' && INCOME_HINT_BY_KIND[ak]) || (ft && FLOW_HINTS[ft.value]) || '';
      const list = $('#cat-list');
      const cats = ((window.CATEGORIES || {})[ak] || {})[ft ? ft.value : ''] || [];
      if (list) list.innerHTML = cats.map(c => `<option value="${c}">`).join('');
      const sl = $('[data-source-label]', dlg);
      if (sl) sl.textContent = SOURCE_LABEL[ak] || t('Источник');
      const si = form.querySelector('[name=source]');
      if (si) si.placeholder = SOURCE_PH[ak] || '';
    }
  }

  function openDialog(id, fill) {
    const dlg = document.getElementById(id);
    if (!dlg) return;
    const form = $('form', dlg);
    form.reset();
    $$('input[type=hidden][name=id]', form).forEach(i => (i.value = ''));
    const isEdit = !!(fill && fill.id);
    dlg.classList.toggle('is-edit', isEdit);
    const h = $('[data-title-new]', dlg);
    if (h) h.textContent = isEdit ? h.dataset.titleEdit : h.dataset.titleNew;
    if (fill) for (const [k, v] of Object.entries(fill)) {
      const els = form.querySelectorAll(`[name="${k}"]`);
      els.forEach(el => {
        if (el.type === 'radio') el.checked = String(el.value) === String(v);
        else if (el.type === 'checkbox') el.checked = !!v;
        else el.value = v == null ? '' : (typeof v === 'number' ? String(+v.toFixed(6)).replace('.', ',') : v);
      });
    }
    const del = $('[data-delete-action]', dlg);
    if (del && isEdit) del.setAttribute('formaction', del.dataset.deleteAction.replace(/\/0\//, `/${fill.id}/`));
    syncDialog(dlg);
    dlg.showModal();
    const first = form.querySelector('input:not([type=hidden]):not([type=radio]), select');
    if (first && !isEdit) setTimeout(() => first.focus(), 30);
  }

  document.addEventListener('click', e => {
    const opener = e.target.closest('[data-open]');
    if (opener) {
      e.preventDefault(); e.stopPropagation();
      const menu = opener.closest('details'); if (menu) menu.open = false;
      let fill = null;
      try { fill = opener.dataset.fill ? JSON.parse(opener.dataset.fill) : null; } catch (err) {}
      openDialog(opener.dataset.open, fill);
      return;
    }
    if (e.target.closest('[data-close]')) { e.target.closest('dialog').close(); return; }
    if (e.target.tagName === 'DIALOG') { e.target.close(); return; } // клик по фону
    const cl = e.target.closest('[data-confirm-link]');
    if (cl && !confirm(cl.dataset.confirmLink)) { e.preventDefault(); return; }
    const row = e.target.closest('tr[data-href]');
    if (row && !e.target.closest('a,button,input')) location.href = row.dataset.href;
    $$('details.menu[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
  });
  document.addEventListener('change', e => { const d = e.target.closest('dialog'); if (d) syncDialog(d); });

  /* подтверждение удаления */
  document.addEventListener('submit', e => {
    const msg = (e.submitter && e.submitter.dataset.confirm) || e.target.dataset.confirm;
    if (msg && !confirm(msg)) e.preventDefault();
  });

  /* режим правки цен */
  const pf = $('#prices-form');
  if (pf) {
    $('#edit-prices').addEventListener('click', () => { pf.classList.add('editing'); const i = $('.edit', pf); if (i) i.focus(); });
    $('#cancel-prices').addEventListener('click', () => { pf.reset(); pf.classList.remove('editing'); });
  }

  /* ───────────────────────── графики ───────────────────────── */
  const NS = 'http://www.w3.org/2000/svg';
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (parent) parent.appendChild(n);
    return n;
  };
  const fmtShort = v => {
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1).replace('.0', '').replace('.', ',') + t(' млрд');
    if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace('.0', '').replace('.', ',') + t(' млн');
    if (a >= 1e3) return Math.round(v / 1e3) + t(' тыс');
    return String(Math.round(v));
  };
  const fmtFull = v => Math.round(v).toLocaleString(locale);
  const MONTHS = [t('янв'), t('фев'), t('мар'), t('апр'), t('мая'), t('июн'), t('июл'), t('авг'), t('сен'), t('окт'), t('ноя'), t('дек')];
  const fmtDate = (d, withYear) => `${d.getDate()} ${MONTHS[d.getMonth()]}${withYear ? ' ' + d.getFullYear() : ''}`;
  const parse = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };

  function niceTicks(min, max, n = 4) {
    if (min === max) { max = min * 1.1 || 1; min = min * 0.9; }
    const span = max - min;
    const step0 = span / n;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0);
    const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
    const out = [];
    for (let v = lo; v <= hi + step / 2; v += step) out.push(v);
    return out;
  }

  function history(sel, data, cur, rangeSel) {
    const box = $(sel);
    if (!box) return;
    let days = 0;
    const draw = () => {
      box.innerHTML = '';
      let pts = data.map(d => ({ t: parse(d.date), v: d.value, i: d.invested }));
      if (days) { const from = Date.now() - days * 864e5; pts = pts.filter(p => +p.t >= from); }
      if (pts.length < 2) {
        const msg = document.createElement('div');
        msg.className = 'empty-chart';
        msg.textContent = pts.length ? `${t('Сегодня:')} ${fmtFull(pts[0].v)} ${cur}. ${t('Линия появится со второго дня.')}` : t('Нет данных за период');
        box.appendChild(msg);
        return;
      }
      const W = box.clientWidth, H = box.clientHeight, P = { l: 56, r: 8, t: 10, b: 24 };
      const svg = el('svg', { viewBox: `0 0 ${W} ${H}` }, box);
      const t0 = +pts[0].t, t1 = +pts[pts.length - 1].t;
      const vals = pts.flatMap(p => [p.v, p.i]);
      const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
      const y0 = ticks[0], y1 = ticks[ticks.length - 1];
      const X = t => P.l + (W - P.l - P.r) * ((t - t0) / (t1 - t0 || 1));
      const Y = v => P.t + (H - P.t - P.b) * (1 - (v - y0) / (y1 - y0 || 1));

      const g = el('g', { class: 'grid' }, svg), ax = el('g', { class: 'axis' }, svg);
      ticks.forEach(v => {
        el('line', { x1: P.l, x2: W - P.r, y1: Y(v), y2: Y(v) }, g);
        el('text', { x: P.l - 8, y: Y(v) + 4, 'text-anchor': 'end' }, ax).textContent = fmtShort(v);
      });
      const nx = Math.max(2, Math.min(5, pts.length, Math.floor((W - P.l) / 95))), multiYear = new Date(t0).getFullYear() !== new Date(t1).getFullYear();
      for (let k = 0; k < nx; k++) {
        const t = t0 + (t1 - t0) * k / (nx - 1);
        el('text', { x: X(t), y: H - 6, 'text-anchor': k === 0 ? 'start' : k === nx - 1 ? 'end' : 'middle' }, ax)
          .textContent = fmtDate(new Date(t), multiYear);
      }

      const accent = css('--accent');
      const defs = el('defs', {}, svg), grad = el('linearGradient', { id: sel.slice(1) + '-g', x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
      el('stop', { offset: 0, 'stop-color': accent, 'stop-opacity': .18 }, grad);
      el('stop', { offset: 1, 'stop-color': accent, 'stop-opacity': 0 }, grad);
      const line = pts.map((p, k) => `${k ? 'L' : 'M'}${X(+p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join('');
      el('path', { d: `${line}L${X(t1)},${Y(y0)}L${X(t0)},${Y(y0)}Z`, fill: `url(#${sel.slice(1)}-g)` }, svg);
      el('path', { d: pts.map((p, k) => `${k ? 'L' : 'M'}${X(+p.t)},${Y(p.i)}`).join(''), fill: 'none', stroke: css('--muted'), 'stroke-width': 1.5, 'stroke-dasharray': '4 3' }, svg);
      el('path', { d: line, fill: 'none', stroke: accent, 'stroke-width': 2, 'stroke-linejoin': 'round' }, svg);

      const cross = el('line', { y1: P.t, y2: H - P.b, stroke: css('--muted'), 'stroke-width': 1, opacity: 0 }, svg);
      const dot = el('circle', { r: 4, fill: accent, stroke: css('--surface'), 'stroke-width': 2, opacity: 0 }, svg);
      const tip = document.createElement('div'); tip.className = 'tip'; box.appendChild(tip);
      svg.addEventListener('mousemove', e => {
        const r = svg.getBoundingClientRect(), mx = (e.clientX - r.left) * (W / r.width);
        let best = pts[0];
        pts.forEach(p => { if (Math.abs(X(+p.t) - mx) < Math.abs(X(+best.t) - mx)) best = p; });
        const x = X(+best.t), y = Y(best.v);
        cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('opacity', .4);
        dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('opacity', 1);
        const pr = best.v - best.i;
        tip.innerHTML = `${fmtDate(best.t, true)}<br><b>${fmtFull(best.v)} ${cur}</b><br>${t('вложено')} ${fmtFull(best.i)} · ${pr >= 0 ? '+' : '−'}${fmtFull(Math.abs(pr))}`;
        tip.style.left = Math.min(Math.max(x / W * r.width, 80), r.width - 80) + 'px';
        tip.style.top = y / H * r.height + 'px';
        tip.style.opacity = 1;
      });
      svg.addEventListener('mouseleave', () => { tip.style.opacity = 0; dot.setAttribute('opacity', 0); cross.setAttribute('opacity', 0); });
    };
    draw();
    window.addEventListener('resize', draw);
    if (rangeSel) $(rangeSel).addEventListener('change', e => { days = +e.target.value; draw(); });
  }

  function donut(sel, legendSel, entries, cur) {
    const box = $(sel), legend = $(legendSel);
    if (!box) return;
    const colors = ['--c1', '--c2', '--c3', '--c4', '--c5', '--c6', '--c8', '--c7'];
    const total = entries.reduce((s, [, v]) => s + v, 0);
    const draw = () => {
      box.innerHTML = ''; legend.innerHTML = '';
      const svg = el('svg', { viewBox: '0 0 100 100' }, box);
      const R = 42, w = 13, C = 2 * Math.PI * R;
      el('circle', { cx: 50, cy: 50, r: R, fill: 'none', stroke: css('--surface-2'), 'stroke-width': w }, svg);
      const center = document.createElement('div'); center.className = 'center'; box.appendChild(center);
      const setCenter = (label, v) => center.innerHTML = `<div><b>${total ? Math.round(v / total * 100) : 0}%</b>${label}</div>`;
      let off = 0;
      const arcs = [];
      entries.forEach(([name, v], k) => {
        const len = total ? v / total * C : 0, gap = entries.length > 1 ? Math.min(1.2, len / 3) : 0;
        const color = css(colors[k % colors.length]);
        const a = el('circle', { cx: 50, cy: 50, r: R, fill: 'none', stroke: color, 'stroke-width': w,
          'stroke-dasharray': `${Math.max(len - gap, 0)} ${C}`, 'stroke-dashoffset': -off, transform: 'rotate(-90 50 50)',
          style: 'transition: stroke-width .15s; cursor: default' }, svg);
        off += len;
        const li = document.createElement('li');
        li.innerHTML = `<i style="background:${color}"></i><span>${name}</span><span class="lv">${(v / total * 100).toFixed(1).replace('.', ',')}%</span>`;
        li.title = `${fmtFull(v)} ${cur}`;
        legend.appendChild(li);
        const on = () => { a.setAttribute('stroke-width', w + 4); li.classList.add('hl'); setCenter(name, v); };
        const offf = () => { a.setAttribute('stroke-width', w); li.classList.remove('hl'); setCenter(entries[0][0], entries[0][1]); };
        [a, li].forEach(n => { n.addEventListener('mouseenter', on); n.addEventListener('mouseleave', offf); });
        arcs.push(a);
      });
      if (entries.length) setCenter(entries[0][0], entries[0][1]);
    };
    draw();
    window.addEventListener('resize', () => { if (document.documentElement.dataset.theme !== box.dataset.theme) { box.dataset.theme = document.documentElement.dataset.theme; draw(); } });
  }

  function bars(sel, data, cur) {
    const box = $(sel);
    if (!box) return;
    const draw = () => {
      box.innerHTML = '';
      const W = box.clientWidth, H = box.clientHeight, P = { l: 48, r: 4, t: 8, b: 22 };
      const svg = el('svg', { viewBox: `0 0 ${W} ${H}` }, box);
      const ticks = niceTicks(0, Math.max(...data.map(d => d[1])), 3);
      const top = ticks[ticks.length - 1];
      const Y = v => P.t + (H - P.t - P.b) * (1 - v / top);
      const g = el('g', { class: 'grid' }, svg), ax = el('g', { class: 'axis' }, svg);
      ticks.forEach(v => { el('line', { x1: P.l, x2: W - P.r, y1: Y(v), y2: Y(v) }, g); el('text', { x: P.l - 8, y: Y(v) + 4, 'text-anchor': 'end' }, ax).textContent = fmtShort(v); });
      const bw = (W - P.l - P.r) / data.length;
      const tip = document.createElement('div'); tip.className = 'tip'; box.appendChild(tip);
      data.forEach(([m, v], k) => {
        const x = P.l + k * bw + bw * .2, w = bw * .6;
        const r = el('rect', { x, y: Y(v), width: w, height: Math.max(Y(0) - Y(v), 0), rx: 3, fill: css('--accent') }, svg);
        const [yy, mm] = m.split('-').map(Number);
        el('text', { x: x + w / 2, y: H - 6, 'text-anchor': 'middle' }, ax).textContent = MONTHS[mm - 1];
        r.addEventListener('mouseenter', () => { r.setAttribute('opacity', .8); tip.innerHTML = `${MONTHS[mm - 1]} ${yy}<br><b>${fmtFull(v)} ${cur}</b>`; tip.style.left = (x + w / 2) / W * box.clientWidth + 'px'; tip.style.top = Y(v) / H * box.clientHeight + 'px'; tip.style.opacity = 1; });
        r.addEventListener('mouseleave', () => { r.setAttribute('opacity', 1); tip.style.opacity = 0; });
      });
    };
    draw();
    window.addEventListener('resize', draw);
  }

  /* помесячные столбики с разбивкой по группам */
  const GROUP_COLOR = { broker: '--c1', crypto: '--c4', realty: '--c2', other: '--c5', freelance: '--c3' };
  function stacked(sel, legendSel, months, series, cur) {
    const box = $(sel);
    if (!box) return;
    const draw = () => {
      box.innerHTML = '';
      const W = box.clientWidth, H = box.clientHeight, P = { l: 48, r: 4, t: 8, b: 22 };
      const svg = el('svg', { viewBox: `0 0 ${W} ${H}` }, box);
      const totals = months.map((_, i) => series.reduce((s, x) => s + x.values[i], 0));
      const max = Math.max(...totals, 0);
      if (!max) { const m = document.createElement('div'); m.className = 'empty-chart'; m.textContent = t('Доходов за период пока нет'); box.appendChild(m); return; }
      const ticks = niceTicks(0, max, 3), top = ticks[ticks.length - 1];
      const Y = v => P.t + (H - P.t - P.b) * (1 - v / top);
      const g = el('g', { class: 'grid' }, svg), ax = el('g', { class: 'axis' }, svg);
      ticks.forEach(v => { el('line', { x1: P.l, x2: W - P.r, y1: Y(v), y2: Y(v) }, g); el('text', { x: P.l - 8, y: Y(v) + 4, 'text-anchor': 'end' }, ax).textContent = fmtShort(v); });
      const bw = (W - P.l - P.r) / months.length, w = Math.min(bw * .62, 44);
      const tip = document.createElement('div'); tip.className = 'tip'; box.appendChild(tip);
      const every = bw < 26 ? 2 : 1;
      months.forEach((m, i) => {
        const x = P.l + i * bw + (bw - w) / 2;
        let acc = 0;
        const col = el('g', {}, svg);
        series.forEach((s, k) => {
          const v = s.values[i]; if (!v) return;
          const y1 = Y(acc + v), y0 = Y(acc); acc += v;
          el('rect', { x, y: y1, width: w, height: Math.max(y0 - y1 - (k < series.length - 1 ? 1 : 0), 0.5), rx: 2, fill: css(GROUP_COLOR[s.key] || '--c7') }, col);
        });
        const [yy, mm] = m.split('-').map(Number);
        if (i % every === 0) el('text', { x: x + w / 2, y: H - 6, 'text-anchor': 'middle' }, ax).textContent = MONTHS[mm - 1];
        const hit = el('rect', { x: P.l + i * bw, y: P.t, width: bw, height: H - P.t - P.b, fill: 'transparent' }, svg);
        hit.addEventListener('mouseenter', () => {
          col.setAttribute('opacity', .85);
          const parts = series.filter(s => s.values[i]).map(s => `${s.name}: ${fmtFull(s.values[i])}`).join('<br>');
          tip.innerHTML = `${MONTHS[mm - 1]} ${yy}<br><b>${fmtFull(totals[i])} ${cur}</b>${parts && series.length > 1 ? '<br>' + parts : ''}`;
          tip.style.left = Math.min(Math.max((x + w / 2) / W * box.clientWidth, 70), box.clientWidth - 70) + 'px';
          tip.style.top = Y(totals[i]) / H * box.clientHeight + 'px'; tip.style.opacity = 1;
        });
        hit.addEventListener('mouseleave', () => { col.setAttribute('opacity', 1); tip.style.opacity = 0; });
      });
      const lg = legendSel && $(legendSel);
      if (lg) lg.innerHTML = series.map(s => `<span><i class="sw sq" style="background:${css(GROUP_COLOR[s.key] || '--c7')}"></i>${s.name}</span>`).join('');
    };
    draw();
    window.addEventListener('resize', draw);
  }

  window.Charts = { history, donut, bars, stacked };
})();
