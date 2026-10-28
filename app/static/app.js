/* Трип-агент: клиент без сборки. Рендерит ответы агента (текст + блоки) и панель состояния. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* noop */ } },
  };
  let userId = store.get("ta_user") || null;
  let sessionId = store.get("ta_session") || null;
  let busy = false;
  let feed = [];

  const rub = (n) => (Math.round(n) || 0).toLocaleString("ru-RU").replace(/,/g, " ") + " ₽";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])_(.+?)_(?=$|[\s.,)])/g, "$1<i>$2</i>").replace(/\n/g, "<br>");
  const hm = (iso) => iso.slice(11, 16);
  const dm = (iso) => { const d = new Date(iso.slice(0, 10) + "T00:00:00"); return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }); };
  const dur = (m) => { const h = Math.floor(m / 60), mm = m % 60; return h ? `${h} ч${mm ? " " + String(mm).padStart(2, "0") + " мин" : ""}` : `${mm} мин`; };
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };

  // ------------------------------------------------------------------ API
  async function api(path, body, method) {
    const res = await fetch(path, {
      method: method || (body ? "POST" : "GET"),
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  }

  async function newSession() {
    const r = await api("/api/session", { user_id: userId });
    userId = r.user_id; sessionId = r.session_id;
    store.set("ta_user", userId); store.set("ta_session", sessionId);
    feed = [];
    $("#messages").innerHTML = "";
    r.messages.forEach((m) => addAgent(m));
    renderSummary(r.summary);
    renderTrace([]);
  }

  async function send(message, action, label) {
    if (busy) return;
    busy = true; $("#sendBtn").disabled = true;
    addUser(label || message || "…");
    const typing = h(`<div class="msg agent"><div class="bubble typing">Агент думает</div></div>`);
    $("#messages").appendChild(typing); scrollDown();
    try {
      const r = await api("/api/chat", { session_id: sessionId, user_id: userId, message, action });
      typing.remove();
      if (r.session_id !== sessionId) { sessionId = r.session_id; store.set("ta_session", sessionId); }
      r.messages.forEach((m) => addAgent(m));
      renderSummary(r.summary);
      renderTrace(r.trace || [], r.messages[0] && r.messages[0].elapsed_ms);
    } catch (e) {
      typing.remove();
      addAgent({ text: "Сервер не ответил. Проверьте, что контейнер запущен, и попробуйте ещё раз.", blocks: [], chips: [] });
    } finally {
      busy = false; $("#sendBtn").disabled = false; $("#input").focus();
    }
  }

  // ------------------------------------------------------------------ лента
  function persist() {
    const keep = feed.slice(-24);
    try { sessionStorage.setItem("ta_feed_" + sessionId, JSON.stringify(keep)); } catch (e) { /* noop */ }
  }
  function addUser(text) {
    feed.push({ role: "user", text }); persist();
    $("#messages").appendChild(h(`<div class="msg user"><div class="bubble">${esc(text)}</div></div>`));
    $("#chips").innerHTML = "";
    scrollDown();
  }
  const KINDS = ["plan", "cart", "destinations"];
  const kindOf = (m) => ((m.blocks || []).find((b) => KINDS.includes(b.type)) || {}).type || "";
  function buildAgent(m, idx) {
    const el = h(`<div class="msg agent" data-feed="${idx}" data-kind="${kindOf(m)}"><div class="bubble">${md(m.text)}</div></div>`);
    (m.blocks || []).forEach((b) => { const node = renderBlock(b); if (node) el.appendChild(node); });
    return el;
  }
  function addAgent(m, restoring, idx) {
    if (!restoring) { feed.push({ role: "agent", m }); persist(); idx = feed.length - 1; }
    $("#messages").appendChild(buildAgent(m, idx));
    renderChips(m.chips || []);
    scrollDown();
  }
  // Тихое обновление: заменяем последнюю карточку того же вида, в ленту ничего не добавляем
  function replaceAgent(m) {
    const kind = kindOf(m);
    if (!kind || !m.text) return;
    const all = document.querySelectorAll(`.msg.agent[data-kind="${kind}"]`);
    const old = all[all.length - 1];
    if (!old) return;
    const idx = +old.dataset.feed;
    const el = buildAgent(m, idx);
    const wasLast = !old.nextElementSibling;
    // сохраняем раскрытые блоки («Сравнить отели», «Сравнить маршруты», проверки) — по порядку и по заголовку
    const openNow = [...old.querySelectorAll("details")].map((d) => ({ open: d.open, title: summaryKey(d) }));
    const fresh = [...el.querySelectorAll("details")];
    fresh.forEach((d, i) => {
      const same = openNow.find((x) => x.title === summaryKey(d)) || openNow[i];
      if (same && same.open) d.open = true;
    });
    old.replaceWith(el);
    if (feed[idx]) { feed[idx] = { role: "agent", m }; persist(); }
    if (wasLast) renderChips(m.chips || []);
  }
  const summaryKey = (d) => ((d.querySelector("summary") || {}).textContent || "").replace(/\(\d+\)/, "").trim();
  async function tweak(action) {
    if (busy) return;
    busy = true; document.body.classList.add("updating");
    try {
      const r = await api("/api/chat", { session_id: sessionId, user_id: userId, action: { ...action, silent: true } });
      r.messages.forEach(replaceAgent);
      renderSummary(r.summary);
      renderTrace(r.trace || [], r.messages[0] && r.messages[0].elapsed_ms);
    } catch (e) { /* настройка не применилась — состояние на сервере не изменилось */ }
    finally { busy = false; document.body.classList.remove("updating"); }
  }
  const isTweak = (a) => a && (["set_rate", "cart_transfer", "toggle_addon"].includes(a.type) || (a.type === "set" && a.key === "priority"));
  function scrollDown() { const box = $("#messages"); requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; }); }

  function renderChips(chips) {
    const box = $("#chips"); box.innerHTML = "";
    chips.forEach((c) => {
      const b = h(`<button class="chip">${esc(c.label)}</button>`);
      b.onclick = () => (isTweak(c.action) ? tweak(c.action) : c.action ? send(null, c.action, c.label) : send(c.message, null, c.label));
      box.appendChild(b);
    });
  }
  const act = (action, label) => send(null, action, label);

  // ------------------------------------------------------------------ блоки
  function renderBlock(b) {
    switch (b.type) {
      case "intro": return h(`<div class="steps">${b.steps.map((x, i) => `<div class="step"><div class="step-ic">${x.icon}</div><div><b>${i + 1}. ${esc(x.title)}</b><br><span class="muted">${esc(x.text)}</span></div></div>`).join("")}</div>`);
      case "destinations": return destBlock(b);
      case "plan": return planBlock(b.plan);
      case "diff": return h(`<div class="diff"><b>Что изменилось:</b><ul class="list info">${b.items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`);
      case "hotels": return hotelsTable(b.rows, b.excluded);
      case "flights": return flightsBlock(b.out, b.back);
      case "weights": return weightsBlock(b.weights);
      case "cart": return cartBlock(b.cart);
      case "checkout": return checkoutBlock(b);
      case "booking": return bookingBlock(b.booking);
      case "reminders": return remindersBlock(b.items, b.ics);
      default: return null;
    }
  }

  function destBlock(b) {
    const wrap = h(`<div></div>`);
    const grid = h(`<div class="grid"></div>`);
    b.items.forEach((d, i) => {
      const w = d.weather;
      const card = h(`<div class="card ${i === 0 ? "lead" : ""}">
        <div class="sub">${i === 0 ? "★ Лучшее совпадение · " : ""}${esc(d.country)}</div>
        <h3>${d.flag} ${esc(d.name)}</h3>
        <div class="facts">
          <span class="fact">${w.air > 10 ? "☀️" : "❄️"} ${w.air > 0 ? "+" : ""}${w.air}°</span>
          ${w.sea !== null ? `<span class="fact">🌊 +${w.sea}°</span>` : ""}
          ${w.rain_days >= 6 ? `<span class="fact">🌧 ${w.rain_days} дн.</span>` : ""}
          <span class="fact">${d.travel.mode === "train" ? "🚆" : "✈️"} ${esc(d.travel.text)}</span>
          <span class="fact">🛂 ${esc(d.visa.split(",")[0])}</span>
        </div>
        <div><span class="price">${rub(d.est.total)}</span> <small class="muted">под ключ · ${esc(d.dates.text)}, ${d.est.nights} ноч.</small></div>
        <div class="sub">≈ ${rub(d.est.per_person)} на человека · на месте ~${rub(d.est.on_site_per_day)}/день на всех</div>
        <ul class="list pros">${d.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        ${d.cons.length ? `<ul class="list cons">${d.cons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        ${d.risks.length ? `<ul class="list risks">${d.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <div class="sub">Чем заняться: ${esc(d.highlights.slice(0, 2).join("; "))}</div>
        <div class="actions"><button class="primary small">Выбрать</button></div>
      </div>`);
      $(".actions button", card).onclick = () => act({ type: "choose_destination", dest_id: d.dest_id }, `Выбираю ${d.name}`);
      grid.appendChild(card);
    });
    wrap.appendChild(grid);
    if (b.excluded && b.excluded.length) {
      wrap.appendChild(h(`<details class="excluded" style="margin-top:8px"><summary>Не предложил (${b.excluded.length}) — и почему</summary>
        <ul class="list cons">${b.excluded.map((e) => `<li>${e.flag} ${esc(e.name)} — ${esc(e.reason)}</li>`).join("")}</ul></details>`));
    }
    return wrap;
  }

  function legLine(t, label) {
    const icon = t.mode === "train" ? "🚆" : "✈️";
    const stops = t.mode === "plane" ? (t.stops ? `, пересадка ${esc(t.via)} ${dur(t.layover_min)}` : ", прямой") : `, ${esc(t.class_name)}`;
    const nextDay = t.arr.slice(0, 10) !== t.dep.slice(0, 10) ? ` <span class="muted">(${dm(t.arr)})</span>` : "";
    return `<div class="seg"><span class="ic">${icon}</span><div><b>${label}: ${dm(t.dep)} ${hm(t.dep)} → ${hm(t.arr)}</b>${nextDay}<br>
      <span class="muted">${esc(t.carrier)} ${esc(t.number)} · ${dur(t.duration_min)}${stops}${t.mode === "plane" ? (t.baggage_included ? " · багаж включён" : " · + багаж") : ""}</span></div></div>`;
  }

  function checksHtml(issues, withFix) {
    if (!issues || !issues.length) return `<div class="check ok">✓ Всё состыковано</div>`;
    const icon = { ok: "✓", info: "ℹ", warn: "⚠", error: "✕" };
    const sorted = [...issues].sort((a, b) => ["error", "warn", "ok", "info"].indexOf(a.level) - ["error", "warn", "ok", "info"].indexOf(b.level));
    const hasProblem = sorted.some((i) => i.level === "error" || i.level === "warn");
    const head = hasProblem ? "" : `<div class="check ok">✓ Прилёт, заселение и трансферы состыкованы</div>`;
    return head + sorted.map((i, n) => `<div class="check ${i.level}">${icon[i.level]} ${esc(i.text)}${withFix && i.fix ? ` <button data-fix="${n}">${esc(i.fix.label)}</button>` : ""}</div>`).join("");
  }

  function planBlock(p) {
    const wrap = h(`<div style="display:flex;flex-direction:column;gap:10px"></div>`);
    const grid = h(`<div class="grid"></div>`);
    p.bundles.forEach((b, i) => {
      const st = b.stay, ho = b.hotel;
      const tin = b.transfer_in;
      const e = b.explain;
      const card = h(`<div class="card ${i === 0 ? "lead" : ""}">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><span class="tag ${b.code}">${esc(b.title)}</span><span class="sub">${esc(b.subtitle)}</span></div>
        <div><span class="price">${rub(b.total)}</span> <small class="muted">≈ ${rub(b.per_person)}/чел.</small></div>
        ${legLine(b.out, "Туда")}
        <div class="seg"><span class="ic">🏨</span><div><b>${esc(ho.name)} ${"★".repeat(ho.stars)}</b><br>
          <span class="muted">${esc(ho.area)} · ${ho.rating} (${ho.reviews} отз.)${ho.beach_m !== null ? ` · до моря ${ho.beach_m} м` : ""}<br>
          ${dm(st.check_in)} – ${dm(st.check_out)}, ${st.nights} ноч. · ${esc(st.meal_name)} · ${esc(st.rate.name)}</span></div></div>
        ${tin ? `<div class="seg"><span class="ic">🚕</span><div><b>${esc(tin.name)}</b> <span class="muted">· ~${dur(tin.duration_min)} · ${rub(tin.price)}</span></div></div>` : ""}
        ${legLine(b.back, "Обратно")}
        <div class="section-label">Почему подходит</div>
        <ul class="list pros">${e.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        ${e.tradeoffs.length ? `<div class="section-label">Чем пожертвовали</div><ul class="list cons">${e.tradeoffs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        ${e.risks.length ? `<div class="section-label">Риски</div><ul class="list risks">${e.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <details><summary>Проверка состыковок</summary><div class="checks">${checksHtml(b.issues)}</div>
          ${b.adjustments && b.adjustments.length ? `<div class="sub" style="margin-top:6px">Агент подстроил: ${esc(b.adjustments.join("; "))}</div>` : ""}</details>
        <div class="actions"><button class="primary small">Выбрать и в корзину</button></div>
      </div>`);
      $(".actions button", card).onclick = () => act({ type: "choose_bundle", idx: i }, `Беру «${b.title}»`);
      grid.appendChild(card);
    });
    wrap.appendChild(grid);
    if (p.dest_risks && p.dest_risks.length) {
      wrap.appendChild(h(`<div class="sub">⚠ ${esc(p.dest_risks.join(" · "))}</div>`));
    }
    const hd = h(`<details><summary>Сравнить отели (${p.hotels.length})</summary></details>`);
    hd.appendChild(hotelsTable(p.hotels, p.excluded_hotels, true));
    wrap.appendChild(hd);
    const fd = h(`<details><summary>Сравнить маршруты</summary></details>`);
    fd.appendChild(flightsBlock(p.flights_out, p.flights_back, true));
    wrap.appendChild(fd);
    return wrap;
  }

  function hotelsTable(rows, excluded, inner) {
    const wrap = h(`<div class="${inner ? "" : "card"}"><div class="table-wrap"><table>
      <thead><tr><th>Отель</th><th>Цена за всё</th><th>Подходит</th><th>Питание · тариф</th><th>Расположение</th><th>Плюсы / минусы</th><th></th></tr></thead><tbody></tbody></table></div></div>`);
    const tb = $("tbody", wrap);
    rows.forEach((r) => {
      const tr = h(`<tr class="${r.in_plan ? "in-plan" : ""}"><td><b>${esc(r.name)}</b><br><span class="muted">${"★".repeat(r.stars)} · ${r.rating} (${r.reviews})</span></td>
        <td><b>${rub(r.price)}</b></td>
        <td>${r.fit}%<div class="fitbar"><i style="width:${r.fit}%"></i></div></td>
        <td>${esc(r.meal)}<br><span class="muted">${esc(r.rate_name)}</span></td>
        <td>${esc(r.area)}${r.beach_m !== null ? `<br><span class="muted">море ${r.beach_m} м</span>` : `<br><span class="muted">центр ${r.center_km} км</span>`}${r.transfer_min ? `<br><span class="muted">трансфер ${dur(r.transfer_min)}</span>` : ""}</td>
        <td><ul class="list pros">${r.pros.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><ul class="list cons">${r.cons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></td>
        <td>${r.in_plan ? `<span class="tag optimal">в плане</span>` : `<button class="small">Выбрать</button>`}</td></tr>`);
      const btn = $("button", tr);
      if (btn) btn.onclick = () => act({ type: "swap_hotel", hotel_id: r.id }, `Хочу отель ${r.name}`);
      tb.appendChild(tr);
    });
    if (excluded && excluded.length) {
      wrap.appendChild(h(`<div class="excluded" style="margin-top:6px">Отсеяно честно: ${excluded.map((e) => `${esc(e.name)} — ${esc(e.reason)}`).join("; ")}</div>`));
    }
    return wrap;
  }

  function flightsBlock(out, back, inner) {
    const wrap = h(`<div class="${inner ? "" : "card"}"></div>`);
    [["Туда", out, "swap_out"], ["Обратно", back, "swap_back"]].forEach(([label, list, type]) => {
      const t = h(`<div class="table-wrap"><div class="section-label">${label}</div><table><thead><tr><th>Время</th><th>Рейс</th><th>В пути</th><th>Цена на всех</th><th>Оценка</th><th></th></tr></thead><tbody></tbody></table></div>`);
      list.forEach((f) => {
        const tr = h(`<tr><td><b>${dm(f.dep)} ${hm(f.dep)} → ${hm(f.arr)}</b>${f.arr.slice(0, 10) !== f.dep.slice(0, 10) ? ` <span class="muted">(${dm(f.arr)})</span>` : ""}</td>
          <td>${f.mode === "train" ? "🚆" : "✈️"} ${esc(f.carrier)} ${esc(f.number)}<br><span class="muted">${esc(f.fare_name)}</span></td>
          <td>${dur(f.duration_min)}${f.stops ? `<br><span class="muted">пересадка ${esc(f.via)}</span>` : ""}</td>
          <td><b>${rub(f.total_price)}</b></td>
          <td><ul class="list pros">${f.pros.slice(0, 2).map((x) => `<li>${esc(x)}</li>`).join("")}</ul><ul class="list cons">${f.cons.slice(0, 2).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></td>
          <td><button class="small">Выбрать</button></td></tr>`);
        $("button", tr).onclick = () => act({ type, id: f.id }, `${label}: ${f.number} в ${hm(f.dep)}`);
        $("tbody", t).appendChild(tr);
      });
      wrap.appendChild(t);
    });
    return wrap;
  }

  function weightsBlock(w) {
    const parts = [["Цена", w.price, "#0a7cff"], ["Отель под вас", w.hotel, "#16a06b"], ["Удобство рейсов", w.transport, "#6b4de6"], ["Время в пути", w.time, "#c77800"]];
    return h(`<div class="weights">${parts.map(([l, v, c]) => `<div style="width:${v * 100}%;background:${c}">${v >= 0.12 ? `${l} ${Math.round(v * 100)}%` : ""}</div>`).join("")}</div>`);
  }

  function cartBlock(c) {
    const card = h(`<div class="card"><div class="sub">${esc(c.dest_name)} · ${esc(c.dates_text)} · ${c.party.adults} взр.${c.party.children.length ? " + дети " + c.party.children.join(", ") + " л." : ""}</div>
      <div class="cart-items"></div><div class="section-label">Добавить</div><div class="addons"></div>
      <div class="section-label">Проверка состыковок</div><div class="checks"></div>
      <details><summary>План по дням</summary><div class="timeline"></div></details>
      <div class="total-row"><span>Итого</span><span>${rub(c.total)}</span></div>
      <div class="actions"><button class="primary">Оформить и оплатить</button></div></div>`);
    const items = $(".cart-items", card);
    c.items.forEach((it) => {
      const row = h(`<div class="cart-item"><div><div class="t">${esc(it.title)}</div><div class="d">${esc(it.details)}</div></div><div class="p">${rub(it.price)}</div></div>`);
      if (it.kind === "hotel" && it.rates && it.rates.length > 1) {
        const sel = h(`<select aria-label="Тариф">${it.rates.map((r) => `<option value="${r.code}" ${r.code === it.rate_code ? "selected" : ""}>${esc(r.name)} — ${rub(r.price)}</option>`).join("")}</select>`);
        sel.onchange = () => tweak({ type: "set_rate", code: sel.value });
        row.appendChild(sel);
      }
      if (it.kind === "transfer" && it.options) {
        const sel = h(`<select aria-label="Трансфер">${it.options.map((o) => `<option value="${o.id}" ${o.available ? "" : "disabled"} ${it.title.includes(o.name) ? "selected" : ""}>${esc(o.name)} — ${rub(o.price)}, ~${dur(o.duration_min)}${o.available ? "" : " (не работает в это время)"}</option>`).join("")}<option value="none">Без трансфера</option></select>`);
        sel.onchange = () => tweak({ type: "cart_transfer", dir: it.dir, id: sel.value });
        row.appendChild(sel);
      }
      items.appendChild(row);
    });
    const addons = $(".addons", card);
    c.addons.forEach((a) => {
      const row = h(`<label class="addon"><input type="checkbox" ${a.selected ? "checked" : ""}><span>${esc(a.title)} — <b>${a.price ? rub(a.price) : "бесплатно"}</b><br><span class="muted">${esc(a.note)}</span></span></label>`);
      $("input", row).onchange = () => tweak({ type: "toggle_addon", addon: a.code });
      addons.appendChild(row);
    });
    const checks = $(".checks", card);
    checks.innerHTML = checksHtml(c.issues, true);
    checks.querySelectorAll("button[data-fix]").forEach((btn) => {
      const issue = [...c.issues].sort((a, b) => ["error", "warn", "ok", "info"].indexOf(a.level) - ["error", "warn", "ok", "info"].indexOf(b.level))[+btn.dataset.fix];
      btn.onclick = () => {
        const fx = issue.fix.action;
        if (fx === "add_transfer") return tweak({ type: "cart_transfer", dir: "in", id: (c.items.find((x) => x.kind === "transfer") || { options: [{}] }).options[0].id });
        const code = fx === "early_checkin" && !c.addons.some((a) => a.code === "early_checkin") ? "night_before" : fx;
        tweak({ type: "toggle_addon", addon: code });
      };
    });
    $(".timeline", card).innerHTML = c.timeline.map((d) => `<div class="day"><div class="dt">${dm(d.date)}</div><div><b>${esc(d.title)}</b><br>${d.items.map(esc).join("<br>")}</div></div>`).join("");
    $(".actions button", card).onclick = () => act({ type: "checkout" }, "Оформить");
    return card;
  }

  function checkoutBlock(b) {
    const card = h(`<form class="card form-grid"><div class="section-label">Путешественники</div></form>`);
    b.travelers.forEach((t, i) => {
      card.appendChild(h(`<label>${esc(t.label)} — имя и фамилия латиницей, как в паспорте<input name="t${i}" value="${esc(t.name)}" placeholder="IVAN PETROV" required></label>`));
    });
    card.appendChild(h(`<label>Email для билетов<input name="email" type="email" value="${esc(b.email || "")}" placeholder="you@example.com"></label>`));
    card.appendChild(h(`<label>Карта (имитация оплаты)<input name="card" value="4242 4242 4242 4242" inputmode="numeric"></label>`));
    card.appendChild(h(`<div class="total-row"><span>К оплате</span><span>${rub(b.total)}</span></div>`));
    const btn = h(`<button class="primary" type="submit">Оплатить ${rub(b.total)}</button>`);
    card.appendChild(btn);
    card.onsubmit = (ev) => {
      ev.preventDefault();
      const fd = new FormData(card);
      const travelers = b.travelers.map((t, i) => ({ name: String(fd.get("t" + i) || "").trim(), ...(t.age !== undefined ? { age: t.age } : {}) }));
      if (travelers.some((t) => !t.name)) { alert("Заполните имена всех путешественников"); return; }
      btn.disabled = true;
      act({ type: "pay", travelers, email: String(fd.get("email") || "") }, "Оплатить");
    };
    return card;
  }

  function bookingBlock(bk) {
    const req = Object.entries(bk.requests || {}).map(([k, v]) => `${k === "early_checkin" ? "Ранний заезд" : k === "late_checkout" ? "Поздний выезд" : k}: <span class="status ${v}">${v === "confirmed" ? "подтверждён" : "ждём ответ отеля"}</span>`).join("<br>");
    return h(`<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center"><span class="booking-num">${esc(bk.id)}</span><span class="status ${bk.status}">${bk.status === "confirmed" ? "оплачено" : "отменено"}</span></div>
      <div><b>${esc(bk.dest_name)}</b>, ${esc(bk.dates_text)} · ${rub(bk.total)}</div>
      <div class="seg"><span class="ic">✈️</span><div>${esc(bk.out)}<br>${esc(bk.back)}</div></div>
      <div class="seg"><span class="ic">🏨</span><div>${esc(bk.hotel)}${bk.addons.length ? `<br><span class="muted">+ ${esc(bk.addons.join(", "))}</span>` : ""}</div></div>
      ${bk.transfer ? `<div class="seg"><span class="ic">🚕</span><div>${esc(bk.transfer)}</div></div>` : ""}
      <div class="seg"><span class="ic">👤</span><div>${bk.travelers.map((t) => esc(t.name)).join(", ")}</div></div>
      ${req ? `<div class="seg"><span class="ic">📝</span><div>${req}</div></div>` : ""}
      <details><summary>Напоминания (${bk.reminders.length})</summary><ul class="list info">${bk.reminders.map((r) => `<li><b>${dm(r.at)} ${hm(r.at)}</b> — ${esc(r.text)}</li>`).join("")}</ul>
        <a href="/api/bookings/${encodeURIComponent(bk.id)}/ics">Скачать в календарь (.ics)</a></details>
      <details><summary>План по дням</summary><div class="timeline">${bk.timeline.map((d) => `<div class="day"><div class="dt">${dm(d.date)}</div><div><b>${esc(d.title)}</b><br>${d.items.map(esc).join("<br>")}</div></div>`).join("")}</div></details>
      ${bk.history.length > 1 ? `<details><summary>История изменений</summary><ul class="list info">${bk.history.map((x) => `<li>${esc(x.text)}</li>`).join("")}</ul></details>` : ""}
    </div>`);
  }

  function remindersBlock(items, ics) {
    return h(`<div class="card"><ul class="list info">${items.map((r) => `<li><b>${dm(r.at)} ${hm(r.at)}</b> — ${esc(r.text)}</li>`).join("")}</ul><a href="${esc(ics)}">Скачать в календарь (.ics)</a></div>`);
  }

  // ------------------------------------------------------------------ панель
  // ------------------------------------------------------------------ форма «Что я понял»
  let formDirty = false;
  function fillForm(f) {
    if (!f || formDirty) return;
    const form = $("#tripForm");
    const set = (name, v) => { const el = form.elements[name]; if (el && document.activeElement !== el) el.value = v ?? ""; };
    set("origin", f.origin); set("destination", f.destination);
    set("date_from", f.date_from); set("date_to", f.date_to);
    set("adults", f.adults); set("children", f.children);
  }
  function formSummary(v) {
    const bits = [];
    if (v.origin) bits.push(`откуда: ${v.origin}`);
    bits.push(v.destination ? v.destination : "куда — подобрать");
    if (v.date_from) bits.push(`${dm(v.date_from)}${v.date_to ? " – " + dm(v.date_to) : ""}`);
    if (v.adults) bits.push(`${v.adults} взр.` + (+v.children ? `, детей: ${v.children}` : ""));
    return "Мои параметры: " + bits.join("; ");
  }
  function initForm() {
    const form = $("#tripForm");
    form.addEventListener("input", (e) => {
      formDirty = true;
      const n = e.target.name;
    });
    form.onsubmit = (e) => {
      e.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      formDirty = false;
      closePanel();
      send(null, { type: "apply_form", values }, formSummary(values));
    };
  }
  function renderSummary(s) {
    fillForm(s.form);
    document.querySelectorAll("#priority button").forEach((b) => b.classList.toggle("on", b.dataset.p === s.priority));
    const cc = $("#cartCard");
    if (s.cart) { cc.hidden = false; $("#cartMini").innerHTML = `<div>${esc(s.cart.title)}</div><div class="price">${rub(s.cart.total)}</div>`; }
    else cc.hidden = true;
  }
  function renderTrace(trace, ms) {
    const ol = $("#trace");
    $("#elapsed").textContent = ms !== undefined ? `${ms} мс` : "";
    if (!trace.length) { ol.innerHTML = `<li class="muted">Здесь видно, что агент понял, какие инструменты вызвал и почему.</li>`; return; }
    ol.innerHTML = trace.map((t) => t.kind === "tool"
      ? `<li class="tool"><code>${esc(t.tool)}</code> ${esc(Object.entries(t.args || {}).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length)).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join(" "))}<br><span class="muted">→ ${esc(t.result)}</span></li>`
      : `<li class="think">${esc(t.text)}</li>`).join("");
  }

  // ------------------------------------------------------------------ модалки
  function openModal(html) { $("#modalBody").innerHTML = ""; $("#modalBody").appendChild(typeof html === "string" ? h(`<div>${html}</div>`) : html); $("#modal").hidden = false; }
  function closeModal() { $("#modal").hidden = true; }
  async function openTrips() {
    const list = await api(`/api/bookings?user_id=${encodeURIComponent(userId)}`);
    const box = h(`<div><h2>Мои поездки</h2></div>`);
    if (!list.length) box.appendChild(h(`<p class="muted">Пока нет оформленных поездок.</p>`));
    list.forEach((bk) => box.appendChild(bookingBlock(bk)));
    openModal(box);
  }
  async function openMemory() {
    const p = await api(`/api/profile/${encodeURIComponent(userId)}`);
    const rows = [];
    if (p.origin) rows.push(["Город вылета", p.origin]);
    if (p.adults) rows.push(["Обычно едете", `${p.adults} взр.` + (p.children ? " + дети " + p.children.map((c) => c.age).join(", ") + " л." : "")]);
    if (p.pets) rows.push(["Питомец", p.pets]);
    if (p.meal) rows.push(["Питание", p.meal]);
    if (p.free_cancel) rows.push(["Отмена", "предпочитаете бесплатную отмену"]);
    if (p.priority) rows.push(["Приоритет", p.priority]);
    if (p.prefs) rows.push(["Интересы", Object.entries(p.prefs).sort((a, b) => b[1] - a[1]).map(([k]) => k).join(", ")]);
    if (p.trips) rows.push(["Поездки", p.trips.map((t) => `${t.dest_name} (${t.dates})`).join("; ")]);
    if (p.travelers) rows.push(["Путешественники", p.travelers.map((t) => t.name).join(", ")]);
    const box = h(`<div><h2>Что агент помнит о вас</h2><p class="muted">Память хранится между сессиями и подставляется в следующие поездки: город вылета, состав, возраст детей, предпочтения, данные путешественников.</p>
      <table><tbody>${rows.length ? rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("") : `<tr><td class="muted">Пока пусто — оформите поездку или расскажите о себе.</td></tr>`}</tbody></table>
      <p><button class="small" id="forget">Забыть меня</button> <span class="muted">ID: ${esc(userId)}</span></p></div>`);
    $("#forget", box).onclick = async () => { await api(`/api/profile/${encodeURIComponent(userId)}`, null, "DELETE"); closeModal(); };
    openModal(box);
  }
  async function openMetrics() {
    const m = await api("/api/metrics");
    const kp = (o) => `<div class="kpis">
      <div class="kpi"><div class="v">${o.sessions}</div><div class="l">сессий</div></div>
      <div class="kpi"><div class="v">${o.completion_rate === null ? "—" : Math.round(o.completion_rate * 100) + "%"}</div><div class="l">доведено до покупки</div></div>
      <div class="kpi"><div class="v">${o.avg_clarifications ?? "—"}</div><div class="l">уточнений на сессию</div></div>
      <div class="kpi"><div class="v">${o.median_time_to_first_reco_s ?? "—"} с</div><div class="l">до первой рекомендации (медиана)</div></div>
      <div class="kpi"><div class="v">${o.avg_turns_to_booking ?? "—"}</div><div class="l">ходов до покупки</div></div>
      <div class="kpi"><div class="v">${o.connection_errors_in_bookings}</div><div class="l">ошибок состыковки в бронях</div></div>
      <div class="kpi"><div class="v">${o.replans}</div><div class="l">пересборок плана</div></div>
      <div class="kpi"><div class="v">${o.avg_check ? rub(o.avg_check) : "—"}</div><div class="l">средний чек</div></div></div>`;
    const box = h(`<div><h2>Метрики качества агента</h2><div class="section-label">Живые сессии</div>${kp(m.live)}
      <div class="section-label">Тестовый набор</div>${kp(m.eval)}
      <p><button class="primary small" id="runEval">Прогнать тестовый набор</button> <span class="muted" id="evalStatus"></span></p><div id="evalOut"></div></div>`);
    $("#runEval", box).onclick = async () => {
      $("#evalStatus", box).textContent = "Прогоняю сценарии…";
      const r = await api("/api/eval/run", {});
      $("#evalStatus", box).textContent = "";
      const s = r.summary;
      $("#evalOut", box).innerHTML = `<div class="kpis">
        <div class="kpi"><div class="v">${s.passed}/${s.total}</div><div class="l">сценариев пройдено</div></div>
        <div class="kpi"><div class="v">${Math.round(s.completion_rate * 100)}%</div><div class="l">доведено до оплаты</div></div>
        <div class="kpi"><div class="v">${s.avg_clarifications}</div><div class="l">уточнений в среднем</div></div>
        <div class="kpi"><div class="v">${s.median_ttfr_ms} мс</div><div class="l">до первой рекомендации</div></div>
        <div class="kpi"><div class="v">${s.connection_errors}</div><div class="l">ошибок состыковки</div></div>
        <div class="kpi"><div class="v">${s.constraint_violations}</div><div class="l">нарушений условий</div></div></div>
        <div class="table-wrap"><table><thead><tr><th>Сценарий</th><th>Итог</th><th>Уточн.</th><th>Ходов</th><th>До реком.</th><th>Проверки</th></tr></thead><tbody>
        ${r.scenarios.map((x) => `<tr><td>${esc(x.name)}<br><span class="muted">${esc(x.first_message)}</span></td><td class="${x.passed ? "ok-cell" : "bad-cell"}">${x.passed ? "✓" : "✕"} ${esc(x.result)}</td><td>${x.clarifications}</td><td>${x.turns}</td><td>${x.ttfr_ms ?? "—"} мс</td><td>${x.checks.map((c) => `<span class="${c.ok ? "ok-cell" : "bad-cell"}">${c.ok ? "✓" : "✕"}</span> ${esc(c.name)}`).join("<br>")}</td></tr>`).join("")}
        </tbody></table></div>`;
    };
    openModal(box);
  }

  // ------------------------------------------------------------------ панель на мобиле
  function closePanel() { $("#panel").classList.remove("open"); }

  // ------------------------------------------------------------------ тема
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const isDark = () => (document.documentElement.dataset.theme || (darkQuery.matches ? "dark" : "light")) === "dark";
  function showThemeButton() {
    const b = $("#themeToggle");
    const label = isDark() ? "Светлая тема" : "Тёмная тема";
    b.textContent = isDark() ? "☀️" : "🌙";
    b.title = label;
    b.setAttribute("aria-label", label);
  }
  function toggleTheme() {
    const next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
    showThemeButton();
  }

  // ------------------------------------------------------------------ init
  document.addEventListener("DOMContentLoaded", async () => {
    showThemeButton();
    $("#themeToggle").onclick = toggleTheme;
    darkQuery.addEventListener?.("change", showThemeButton);
    const input = $("#input");
    $("#composer").onsubmit = (e) => { e.preventDefault(); const v = input.value.trim(); if (!v) return; input.value = ""; input.style.height = ""; send(v); };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } });
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; });
    $("#newTrip").onclick = () => { closePanel(); if (sessionId) send(null, { type: "reset" }, "Новая поездка"); else newSession(); };
    document.querySelectorAll("#priority button").forEach((b) => b.onclick = () => {
      if (b.classList.contains("on")) return;
      document.querySelectorAll("#priority button").forEach((x) => x.classList.toggle("on", x === b));
      tweak({ type: "set", key: "priority", value: b.dataset.p });
    });
    document.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => { closePanel(); ({ trips: openTrips, memory: openMemory, metrics: openMetrics })[b.dataset.open](); });
    $("#modalClose").onclick = closeModal;
    initForm();
    $("#modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
    $("#panelToggle").onclick = () => $("#panel").classList.toggle("open");
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); closePanel(); } });

    let restored = false;
    if (sessionId) {
      try {
        const raw = sessionStorage.getItem("ta_feed_" + sessionId);
        const info = await api(`/api/session/${encodeURIComponent(sessionId)}`);
        if (raw && info.session_id === sessionId) {
          feed = JSON.parse(raw);
          feed.forEach((x, i) => (x.role === "user" ? $("#messages").appendChild(h(`<div class="msg user"><div class="bubble">${esc(x.text)}</div></div>`)) : addAgent(x.m, true, i)));
          renderSummary(info.summary);
          restored = true;
        }
      } catch (e) { restored = false; }
    }
    if (!restored) await newSession();
    input.focus();
  });
})();
