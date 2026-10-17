(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SGD = window.SB.sgdPerUsd || 1.33;
  const money = (usd) => usd == null ? "–" : "S$" + Math.round(usd * SGD).toLocaleString("en-US");
  const pct = (x, d = 1) => x == null || isNaN(x) ? "–" : (100 * x).toFixed(d) + "%";
  const shortClock = (s) => (s || "").replace("Day ", "D");
  const MODEL = { A: "Payee risk", B: "Behaviour", C: "Coercion" };
  const MACHINES = { "9175": "IBM z17 / LinuxONE Emperor 5", "9176": "IBM z17 (rack)", "3931": "IBM z16 / LinuxONE Emperor 4",
    "3932": "IBM z16 / LinuxONE Rockhopper 4", "8561": "IBM z15 / LinuxONE III", "8562": "IBM z15 T02" };

  const state = { feed: [], cases: new Map(), queue: [], selected: null, token: localStorage.getItem("sb_token") || "",
    dirtyFeed: false, dirtySel: false, proof: null };

  // ------------------------------------------------------------------ stream
  function connect() {
    const es = new EventSource("/api/stream");
    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "decision") onDecision(msg.d);
      else if (msg.type === "metrics") onMetrics(msg.m);
      else if (msg.type === "llm") onLLM(msg);
      else if (msg.type === "episode_stopped") {
        const c = state.cases.get(msg.id);
        if (c) { c.stopped = true; if (state.selected === msg.id) state.dirtySel = true; }
      }
    };
    es.onerror = () => { $("#feed-note").textContent = "reconnecting…"; };
    es.onopen = () => { $("#feed-note").textContent = "IBM AMLworld test days, replayed"; };
  }

  function onDecision(d) {
    state.feed.unshift(d);
    if (state.feed.length > 15) state.feed.length = 15;
    state.dirtyFeed = true;
    if (d.case) {
      const prev = state.cases.get(d.id) || {};
      state.cases.set(d.id, { ...prev, ...d, messages: { ...(d.messages || {}), ...(prev.messages || {}) } });
      state.queue = [d.id, ...state.queue.filter((x) => x !== d.id)].slice(0, 14);
      if (d.scenario) select(d.id);
      renderQueue();
    }
  }

  function onLLM(e) {
    let c = state.cases.get(e.id);
    if (!c) { fetchCase(e.id); return; }
    c.messages = c.messages || {};
    c.messages[e.field] = { text: e.text, done: e.done, source: e.source };
    if (state.selected === e.id) state.dirtySel = true;
  }

  async function fetchCase(id) {
    try {
      const r = await fetch(`/api/case/${encodeURIComponent(id)}`);
      if (!r.ok) return;
      const c = await r.json();
      const prev = state.cases.get(id) || {};
      state.cases.set(id, { ...prev, ...c, messages: { ...(prev.messages || {}), ...(c.messages || {}) } });
      if (state.selected === id) state.dirtySel = true;
    } catch (_) { /* transient */ }
  }

  function select(id) {
    state.selected = id;
    state.dirtySel = true;
    fetchCase(id);
    renderQueue();
  }

  // ------------------------------------------------------------------ render
  function bar(cls, v) {
    if (v == null) return `<span class="muted">—</span>`;
    return `<div class="bar ${cls}"><i style="width:${Math.max(2, Math.min(100, v * 100)).toFixed(0)}%"></i></div>`;
  }

  function renderFeed() {
    $("#feed").innerHTML = state.feed.map((d, i) => {
      const pool = d.pool;
      return `<tr class="${i === 0 ? "new" : ""} ${d.scenario ? "scen" : ""} ${d.case ? "clickable" : ""}" data-id="${esc(d.id)}">
        <td class="mono">${esc(shortClock(d.sim))}</td>
        <td class="r mono">${money(d.amount_usd)}</td>
        <td>${esc(d.fmt)}${d.aml ? ' <span class="chip BLOCK" title="back-office AML flag">AML</span>' : ""}</td>
        <td>${bar("A", d.scores?.A)}</td>
        <td>${pool ? bar("B", d.scores?.B) : bar("B", null)}</td>
        <td>${pool ? bar("C", d.scores?.C) : bar("C", null)}</td>
        <td class="r mono">${pool ? d.score.toFixed(3) : "—"}</td>
        <td><span class="chip ${d.decision}">${d.decision}</span></td>
        <td class="r mono">${d.latency_ms?.toFixed(1) ?? "–"}</td></tr>`;
    }).join("");
  }

  function renderQueue() {
    $("#queue").innerHTML = state.queue.map((id) => {
      const c = state.cases.get(id) || {};
      return `<span class="qitem ${id === state.selected ? "sel" : ""}" data-id="${esc(id)}">${esc(shortClock(c.sim))} · ${esc(c.decision)} · ${money(c.amount_usd)}${c.scenario ? " ★" : ""}</span>`;
    }).join("");
  }

  function fillNames(text, c) {
    const contact = (c.contact || "").split(" (")[0];
    return String(text || "")
      .replace(/[{[<]\s*NAME\s*[}\]>]/g, c.customer || "Customer")
      .replace(/[{[<]\s*CONTACT\s*[}\]>]/g, contact || "your contact")
      .replace(/[{[<]\s*PAYEE\s*[}\]>]/g, c.payee_label || "this payee")
      .replace(/\{[A-Z_]{2,}\}/g, "");
  }

  function msgBlock(c, field, cls) {
    const m = (c.messages || {})[field];
    if (!m) return `<div class="msg ${cls}"><span class="muted">IBM Granite is writing…</span><span class="cursor"></span></div>`;
    const src = m.source === "granite" ? "Written on this LinuxONE server by IBM Granite · no data left the bank"
      : `Vetted template${m.source && m.source !== "template" ? " · " + esc(m.source.replace("template ", "")) : ""}`;
    return `<div class="msg ${cls}">${esc(fillNames(m.text, c))}${m.done ? "" : '<span class="cursor"></span>'}<span class="src">${src}</span></div>`;
  }

  function renderPhone(c) {
    const newPayee = (c.reasons || []).some((r) => r.code === "NEW_PAYEE");
    const banner = { HOLD: "⏸ Transfer paused for your safety", BLOCK: "⛔ Transfer stopped – we will call you",
      NUDGE: "Please double-check this payee", ALLOW: "✓ Sent" }[c.decision];
    let body = "";
    if (c.decision === "HOLD" || c.decision === "BLOCK") {
      body = msgBlock(c, "customer_en", "") + (c.lang === "zh" ? msgBlock(c, "customer_zh", "zh") : "");
    } else if (c.decision === "NUDGE") {
      const top = (c.reasons || [])[0];
      body = `<div class="msg">${esc(top ? top.text : "This payment looks unusual.")}. Only continue if you know and trust this payee.</div>`;
    } else {
      body = `<div class="msg">No unusual risk detected. Decided in ${c.latency_ms?.toFixed(1) ?? "–"} ms.</div>`;
    }
    const actions = (c.decision === "HOLD" || c.decision === "BLOCK")
      ? `<button class="p">Call me back now</button><button class="s">Cancel this transfer</button><button class="d" disabled>Continue anyway · available in 24h</button>`
      : c.decision === "NUDGE" ? `<button class="p">I know this payee – continue</button><button class="s">Cancel</button>`
        : `<button class="s">Done</button>`;
    $("#phone").innerHTML = `
      <div class="appbar"><span>Demo Bank</span><span class="muted">${esc(shortClock(c.sim))}</span></div>
      <div class="xfer"><div class="to">Transfer to</div>
        <div class="payee">${esc(c.payee_label)}${newPayee ? '<span class="tagnew">NEW PAYEE</span>' : ""}</div>
        <div class="amt">${money(c.amount_usd)}</div>
        <div class="meta">${esc(c.fmt)}${c.steps > 1 ? ` · payment ${c.step} of ${c.steps}` : ""}${c.stopped ? " · later payments prevented" : ""}</div></div>
      <div class="msgs"><div class="banner ${c.decision}">${banner}</div>${body}</div>
      <div class="actions">${actions}</div>`;
    $("#phone-who").textContent = c.customer ? `${c.customer}${c.scenario ? " · scenario" : ""}` : "";
  }

  function renderSMS(c) {
    const m = (c.messages || {}).contact;
    $("#sms-who").textContent = c.contact || "";
    if (!c.contact) { $("#sms").innerHTML = `<span class="muted">No trusted contact registered for this customer.</span>`; return; }
    if (!m) {
      $("#sms").innerHTML = (c.decision === "HOLD" || c.decision === "BLOCK")
        ? `<span class="muted">Drafting alert to ${esc(c.contact)}…</span><span class="cursor"></span>`
        : `<span class="muted">No alert needed for a ${esc(c.decision)} decision.</span>`;
      return;
    }
    $("#sms").innerHTML = `<b>Demo Bank Alerts → ${esc(c.contact)}</b><br>${esc(fillNames(m.text, c))}${m.done ? "" : '<span class="cursor"></span>'}`;
  }

  function renderCase(c) {
    const th = (window.SB.thresholds || {});
    const reasons = (c.reasons || []).map((r) => `<div class="reason"><span class="pill ${r.model}" title="${MODEL[r.model]}">${r.model}</span>
      <span>${esc(r.text)}</span>${bar(r.model, Math.min(1, r.weight / 3))}</div>`).join("") || `<div class="muted">No strong individual signals.</div>`;
    const sc = c.scores || {};
    const narr = (c.messages || {}).narrative;
    const cites = (c.knowledge || []).map((k) => `[${esc(k.id)}] ${esc(k.source)} – ${esc(k.section)}`).join("<br>");
    const truth = c.truth ? `Ground truth (evaluation only): ${c.truth.is_scam ? "simulated scam" : "legitimate"}${c.truth.is_laund ? " · AMLworld laundering" : ""}` : "";
    const rcv = c.receiver || {};
    $("#case-id").textContent = c.id || "";
    $("#case").innerHTML = `
      <div class="head"><span class="chip ${c.decision}">${c.decision}</span><span class="big">${money(c.amount_usd)}</span>
        <span class="muted">score ${c.score?.toFixed(3)} · ${c.latency_ms?.toFixed(1)} ms · ${esc(c.sim)}</span></div>
      <div class="sub">${esc(c.customer)} → ${esc(c.payee_label)} · ${esc(c.fmt)}</div>
      <div class="sec"><h3>Why this decision</h3>${reasons}</div>
      <div class="sec"><h3>Model scores</h3><div class="scores">
        <span>A · Payee risk</span>${bar("A", sc.A)}<span class="num">${(sc.A ?? 0).toFixed(2)}</span>
        <span>B · Behaviour</span>${bar("B", sc.B)}<span class="num">${(sc.B ?? 0).toFixed(2)}</span>
        <span>C · Coercion</span>${bar("C", sc.C)}<span class="num">${(sc.C ?? 0).toFixed(2)}</span>
      </div><div class="cite">${c.decision === "ALLOW" ? "No scam typology: payment allowed" : `Likely typology: <b>${esc(c.typology_label || "–")}</b> (${pct(c.typology_p, 0)})`}</div></div>
      <div class="sec"><h3>Payee · last 24h</h3><div class="facts">
        <div class="fact"><div class="k">Senders</div><div class="v">${rcv.senders_24h ?? "–"}</div></div>
        <div class="fact"><div class="k">Inflow</div><div class="v">${money(rcv.in_amt_24h)}</div></div>
        <div class="fact"><div class="k">Passed on</div><div class="v">${pct(Math.min(rcv.pass_through ?? 0, 9.99), 0)}</div></div>
      </div></div>
      <div class="sec"><h3>IBM Granite case narrative</h3>
        <div class="narr">${narr ? esc(fillNames(narr.text, c)) + (narr.done ? "" : '<span class="cursor"></span>')
          : c.decision === "ALLOW" ? '<span class="muted">Allowed – no case narrative needed.</span>' : '<span class="muted">Queued for the on-box LLM…</span>'}</div>
        <div class="cite">${cites}</div></div>
      <div class="sec truth">${esc(truth)}</div>`;
  }

  function renderSelected() {
    const c = state.cases.get(state.selected);
    if (!c) return;
    renderPhone(c);
    renderSMS(c);
    renderCase(c);
  }

  // ----------------------------------------------------------------- metrics
  const charts = [];
  function lineChart(el) {
    const c = new Chart(el, {
      type: "line",
      data: { labels: [], datasets: [{ data: [], borderColor: "#3987e5", backgroundColor: "rgba(57,135,229,.10)",
        borderWidth: 2, pointRadius: 0, tension: .3, fill: true, borderCapStyle: "round", borderJoinStyle: "round" }] },
      options: { animation: false, maintainAspectRatio: false, responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { displayColors: false } },
        scales: { x: { display: false },
          y: { beginAtZero: true, border: { color: "#383835" }, grid: { color: "#262b33" },
               ticks: { color: "#8d96a5", font: { family: "IBM Plex Mono", size: 10 }, maxTicksLimit: 4 } } } },
    });
    charts.push(c);
    return c;
  }
  function initChart() { lineChart($("#latchart")); lineChart($("#tpschart")); }
  function pushPoint(c, v) {
    c.data.labels.push(""); c.data.datasets[0].data.push(v);
    if (c.data.labels.length > 90) { c.data.labels.shift(); c.data.datasets[0].data.shift(); }
    c.update("none");
  }

  function onMetrics(m) {
    if (!m || !m.ts) return;
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set("#simclock", m.sim || "–");
    set("#k-tps", Math.round(m.tps).toLocaleString("en-US"));
    set("#k-events", `${(m.events || 0).toLocaleString("en-US")} scored · ${(m.pool || 0).toLocaleString("en-US")} retail digital`);
    set("#k-p50", m.p50_ms?.toFixed(1));
    set("#k-p99", m.p99_ms?.toFixed(1));
    const c = m.counts || {};
    set("#k-allow", (c.ALLOW || 0).toLocaleString("en-US"));
    set("#k-nudge", (c.NUDGE || 0).toLocaleString("en-US"));
    set("#k-hold", (c.HOLD || 0).toLocaleString("en-US"));
    set("#k-block", (c.BLOCK || 0).toLocaleString("en-US"));
    set("#k-scam", m.scam_usd_seen ? pct(m.scam_usd_held / m.scam_usd_seen, 0) : "–");
    set("#k-scam-s", `${m.scam_held || 0}/${m.scam_seen || 0} simulated scam payments · ${money(m.scam_usd_held)}`);
    set("#k-legit", m.legit_seen ? pct(m.legit_held / m.legit_seen, 2) : "–");
    set("#k-audit", (m.audit_records || 0).toLocaleString("en-US"));
    if (charts.length === 2) { pushPoint(charts[0], m.p99_ms); pushPoint(charts[1], m.tps); }
  }

  // --------------------------------------------------------------- controls
  async function post(url, body) {
    const opts = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
    if (state.token) opts.headers["X-Demo-Token"] = state.token;
    let r = await fetch(url, opts);
    if (r.status === 403) {
      const t = prompt("Demo token");
      if (!t) return null;
      state.token = t; localStorage.setItem("sb_token", t);
      opts.headers["X-Demo-Token"] = t;
      r = await fetch(url, opts);
    }
    return r.ok ? r.json() : null;
  }

  document.addEventListener("click", (e) => {
    const scen = e.target.closest(".scen");
    if (scen) { post(`/api/scenario/${scen.dataset.slug}`); return; }
    const rp = e.target.closest("[data-replay]");
    if (rp) {
      const v = rp.dataset.replay;
      post("/api/replay", v === "pause" ? { paused: true } : { paused: false, tps: Number(v) });
      return;
    }
    const q = e.target.closest(".qitem, tr.clickable");
    if (q && q.dataset.id) { select(q.dataset.id); return; }
    if (e.target.id === "proof-btn") openProof();
    if (e.target.id === "proof-close" || e.target.id === "proof-modal") $("#proof-modal").hidden = true;
    if (e.target.id === "audit-verify") verifyAudit();
  });

  // ------------------------------------------------------------------ proof
  function check(ok, title, detail) {
    const cls = ok === true ? "ok" : ok === false ? "no" : "na";
    const icon = ok === true ? "✔" : ok === false ? "✖" : "•";
    return `<div class="check"><div class="t"><span class="${cls}">${icon}</span>${title}</div><div class="d">${detail}</div></div>`;
  }

  async function loadProof() {
    try { state.proof = await (await fetch("/api/proof")).json(); } catch (_) { return null; }
    const p = state.proof, s = p.services || {};
    const set = (k, on) => document.querySelector(`.badge[data-k="${k}"]`)?.classList.toggle("on", !!on);
    set("arch", p.web?.machine === "s390x");
    set("nnpa", p.cpu?.nnpa_flag);
    set("llm", s.genai?.mode === "granite");
    set("luks", (p.host?.luks?.type || "").includes("LUKS"));
    return p;
  }

  async function openProof() {
    $("#proof-modal").hidden = false;
    const p = await loadProof();
    if (!p) return;
    const s = p.services || {}, sc = s.scorer || {}, g = s.genai || {}, h = p.host || {}, m = p.metrics || {};
    const mt = (p.cpu?.processor0 || "").match(/machine\s*=\s*(\w+)/);
    const ab = sc.lstm_ab || {};
    const speed = ab.nnpa_us_per_tx && ab.cpu_us_per_tx ? (ab.cpu_us_per_tx / ab.nnpa_us_per_tx).toFixed(1) : null;
    const zlibs = sc.zdnn_libs || [];
    const archs = ["web", "scorer", "genai", "replayer"].map((k) => `${k}: ${k === "web" ? p.web?.machine : (s[k]?.machine || "–")}`).join(" · ");
    $("#proof-body").innerHTML = [
      check(p.web?.machine === "s390x", "Running on IBM Z (s390x)", esc(archs)),
      check(mt ? true : null, "Processor", esc(mt ? `${MACHINES[mt[1]] || "machine type " + mt[1]}\n${p.cpu.processor0}` : "not reported")),
      check(!!p.cpu?.nnpa_flag, "Telum on-chip AI accelerator (NNPA facility)", "CPU feature flag 'nnpa' in /proc/cpuinfo: " + (p.cpu?.nnpa_flag ? "present" : "absent")),
      check(zlibs.some((l) => /libzdnn/.test(l)) && zlibs.some((l) => /_z_ops/.test(l)), "Scorer has the IBM zDNN libraries loaded", esc(zlibs.join(", ") || sc.accel_probe_error || "none found") + `\nTensorFlow ${esc(sc.tf_version || "")} · ibm-zdnn-plugin kernels`),
      check(speed ? Number(speed) > 1 : null, "LSTM scoring: zDNN plugin vs plain CPU kernels", speed ? `batch ${ab.batch} · with zDNN ${ab.nnpa_us_per_tx} µs/transfer · without ${ab.cpu_us_per_tx} µs/transfer · ${speed}x faster` : "A/B benchmark not run yet"),
      check(g.mode === "granite", "IBM Granite running on this server", esc(`${g.model || "–"} · ${g.server || "llama.cpp"}${g.build ? " (" + g.build + ")" : ""} · ${g.tokens_per_s ?? "–"} tok/s`)),
      check(g.egress_blocked === true, "AI containers cannot reach the internet", "outbound TCP 1.1.1.1:443 from the genai container: " + (g.egress_blocked ? "blocked" : g.egress_blocked === false ? "OPEN" : "not tested")),
      check(h.luks ? true : null, "Data at rest encrypted (LUKS2 / dm-crypt)", esc(h.luks ? `${h.luks.type} · ${h.luks.cipher} · ${h.luks.keysize} bits · ${h.luks.device} → ${h.luks.mount}` : "host report not found")),
      check((p.cpacf_crypto_drivers || []).length > 0, "Crypto on CPACF hardware", esc((p.cpacf_crypto_drivers || []).slice(0, 8).join(", ") || "no s390 crypto drivers listed")),
      check(null, "Tamper-evident audit log", `<button class="btn ghost" id="audit-verify">Verify hash chain now</button> <span id="audit-res"></span>`),
      check(null, "Containerised services", esc(Object.entries(s).map(([k, v]) => `${k}@${v?.host || "?"}`).join(" · ") + (h.containers ? "\n" + h.containers.join("\n") : ""))),
      check(null, "Live performance", esc(`${Math.round(m.tps || 0)} tx/s · p50 ${m.p50_ms?.toFixed(1)} ms · p99 ${m.p99_ms?.toFixed(1)} ms · model batch ${m.batch_ms?.toFixed(1)} ms`)),
    ].join("");
  }

  async function verifyAudit() {
    $("#audit-res").textContent = "verifying…";
    const r = await post("/api/audit/verify");
    $("#audit-res").textContent = r ? (r.ok ? `✔ intact · ${r.records.toLocaleString("en-US")} records · ${r.ms} ms` : `✖ broken at line ${r.first_bad_line}`) : "error";
  }

  // ------------------------------------------------------------------- boot
  async function boot() {
    initChart();
    try {
      const st = await (await fetch("/api/state")).json();
      onMetrics(st.metrics);
      state.queue = (st.cases || []).map((c) => { state.cases.set(c.id, c); return c.id; }).slice(0, 14);
      renderQueue();
    } catch (_) { /* first load before services are up */ }
    loadProof();
    setInterval(loadProof, 15000);
    connect();
    const loop = () => {
      if (state.dirtyFeed) { state.dirtyFeed = false; renderFeed(); }
      if (state.dirtySel) { state.dirtySel = false; renderSelected(); }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
  boot();
})();
