/* Root & Escape: 8D board client. Plain JS, no build step. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x, d = 2) => (x == null ? "–" : (x * 100).toFixed(d) + "%");
  const cap = (x) => String(x).charAt(0).toUpperCase() + String(x).slice(1);
  const motion = () => (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");

  let visitor;
  try { visitor = localStorage.getItem("rae-visitor"); } catch (e) {}
  if (!visitor) {
    visitor = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/[^a-zA-Z0-9-]/g, "").slice(0, 36);
    try { localStorage.setItem("rae-visitor", visitor); } catch (e) {}
  }
  let signer = "";
  try { signer = localStorage.getItem("rae-signer") || ""; } catch (e) {}

  const state = { cases: [], current: null, data: null, live: null, boxOk: false };
  const SHOWS = {  // what each example case demonstrates
    "118": "Shows a crew that looks guilty but isn't",
    "124": "Shows a two-part cause and an overdue gauge",
    "121": "Shows no real cause, so it claims none",
  };
  // Plain-language meanings for the jargon on the page (shown on hover, focus or tap).
  const GLOSS = {
    "8d": ["8D", "The eight-step report a supplier owes its customer after a defect: team, problem, containment, root causes, corrective actions, verification, prevention and closure."],
    containment: ["Containment (D3)", "Stopping suspect parts from reaching the customer's line while the cause is still being found. Here it means exactly which lots to hold, and where they are now."],
    isnot: ["Is / is not", "A comparison of where the defect shows up and where it doesn't: which press, cavity, shift, crew or resin lot, and which dates. The difference points to the cause."],
    occurrence: ["Occurrence cause", "Why the defect was made in the first place."],
    escape: ["Escape cause", "Why inspection didn't catch the defect before it shipped. An 8D needs this as well as the occurrence cause."],
    confounded: ["Confounded", "A factor that only looks guilty because it overlaps with the real cause. Compare inside each press and the difference disappears."],
    challenger: ["Challenger", "Checks written in code that every draft must pass: evidence behind each claim, numbers that match the data, and no blame on people without a split that proves it. A failed draft goes back to the agent."],
    golden: ["Golden sample", "A known-good part photographed the same way, for comparison."],
    signoff: ["Sign-off", "A named person approves D2 to D5. Export stays locked until all four are signed."],
    evidence: ["Evidence", "Each E-number is one stored tool result, such as a query or a statistical test. Click an E-number anywhere on the page to jump to it."],
    datecodes: ["Date codes", "The production dates moulded into the parts. They tell you which days' output is suspect."],
  };
  const RULES = {
    analysis_was_run: "The analysis was run",
    no_claim_without_signal: "No cause without a signal",
    no_abstain_with_signal: "No \"insufficient evidence\" when there is a signal",
    abstention_names_no_cause: "An abstention names no cause",
    requires_occurrence_and_escape: "Both causes are given",
    evidence_must_resolve: "Every citation exists",
    escape_must_explain_detection: "The escape explains why inspection missed it",
    escape_in_evidence: "The escape matches the inspection records",
    no_people_blame_without_stratified_effect: "No blame on people without proof",
    segment_matches_is_is_not: "It names the place the data points to",
    mechanism_named_or_open: "It names a mechanism, or says it's open",
    cause_is_in_the_plant_data: "The cause is something the plant records",
    mechanism_in_evidence: "The mechanism shows in the process data",
    mechanism_fits_defect: "The mechanism can cause this defect",
    numbers_come_from_evidence: "Every number is in the evidence",
    gemini_coherence_check: "Gemini critic: the claim holds together",
  };
  const ruleName = (id) => RULES[id] || String(id).replaceAll("_", " ");
  const term = (key, text) => `<span class="term" data-term="${key}" tabindex="0">${esc(text)}</span>`;

  async function api(path, opts = {}) {
    const res = await fetch(path, { ...opts, headers: { "X-Visitor": visitor, ...(opts.headers || {}) } });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (e) {}
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return res.json();
  }

  // ------------------------------------------------------------------------------------------ meta + list
  async function loadMeta() {
    try {
      const m = await api("/api/meta");
      const mp = $("#mode-pill");
      mp.textContent = m.gemini ? `Gemini · ${m.model} on Agent Platform` : "Deterministic mode (Gemini off)";
      mp.className = "pill " + (m.gemini ? "co" : "warn");
      const q = m.quota;
      $("#quota-pill").textContent = `Live runs today ${q.used_today}/${q.limit_today}`;
      $("#run-btn").textContent = m.gemini ? "Run live with Gemini" : "Re-run (deterministic)";
      state.meta = m;
    } catch (e) { $("#mode-pill").textContent = "Offline"; }
  }

  async function loadCases(selectId, reselect = true) {
    state.cases = await api("/api/cases");
    const ul = $("#case-list");
    ul.innerHTML = "";
    for (const c of state.cases) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.id = c.case_id;
      const tag = c.verdict ? (c.insufficient ? "insufficient evidence" : "8D drafted") : "not run yet";
      const shows = SHOWS[c.case_id] && c.seed ? `<span class="shows">${esc(SHOWS[c.case_id])}</span>` : "";
      b.innerHTML = `<b>${esc(c.case_id)} · ${esc(c.title)}</b>${shows}<span>${esc(c.defect)} · ${esc(tag)}${c.seed ? "" : " · yours"}</span>`;
      b.addEventListener("click", () => selectCase(c.case_id));
      li.appendChild(b);
      ul.appendChild(li);
    }
    if (!reselect) {  // refresh the list only; re-fetching the open case here could overwrite fresh sign-offs
      document.querySelectorAll("#case-list button").forEach((b) => b.classList.toggle("on", b.dataset.id === state.current));
      return;
    }
    const want = selectId || (location.hash.slice(1) || "118");
    if (state.cases.some((c) => c.case_id === want)) selectCase(want); else if (state.cases.length) selectCase(state.cases[0].case_id);
  }

  async function selectCase(id) {
    if (state.live && state.liveId !== id) { state.live.close(); state.live = null; $("#run-btn").disabled = false; $("#run-note").textContent = ""; }
    state.current = id;
    state.boxOk = false;
    try { history.replaceState(null, "", "#" + id); } catch (e) {}
    document.querySelectorAll("#case-list button").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
    const ul = $("#case-list"), on = ul.querySelector("button.on");
    if (on && ul.scrollWidth > ul.clientWidth) ul.scrollTo({ left: on.parentElement.offsetLeft - ul.offsetLeft, behavior: motion() });
    $("#case-title").textContent = "Loading…";
    try {
      const data = await api(`/api/cases/${encodeURIComponent(id)}`);
      if (id !== state.current) return;  // the visitor already moved to another case
      state.data = data;
      render();
    } catch (e) { if (id === state.current) $("#case-title").textContent = "Could not load the case: " + e.message; }
  }

  // ------------------------------------------------------------------------------------------ render
  function render() {
    const { case: c, result: r, approvals } = state.data;
    $("#case-title").textContent = c.title;
    $("#case-meta").innerHTML = `CASE ${esc(c.case_id)} · ${esc(c.customer)} · received ${esc(String(c.received).slice(0, 16).replace("T", " "))} · ${term("datecodes", "date codes")} ${esc(c.date_code_start)} → ${esc(c.date_code_end)}`;
    const photoUrl = (name) => (!name ? "" : name.startsWith("U-") ? `/api/photos/${encodeURIComponent(name)}?v=${encodeURIComponent(visitor)}` : `/static/photos/${encodeURIComponent(name)}`);
    $("#img-defect").src = photoUrl(c.photo) || "";
    $("#img-defect").alt = c.photo ? "Defect photo" : "No photo supplied";
    $("#img-defect").hidden = !c.photo;  // no broken-image icon when the complaint came without a photo
    $("#img-golden").src = photoUrl(c.golden);
    $("#complaint").textContent = c.complaint;
    renderRail();
    if (!r) {
      $("#board").hidden = true; renderVerdict(null);
      let empty = $("#empty");
      if (!empty) { empty = document.createElement("div"); empty.id = "empty"; empty.className = "empty"; $("#board").after(empty); }
      empty.hidden = false;
      empty.textContent = 'Not investigated yet. Press "Run live" to start; each step streams in above.';
      return;
    }
    $("#board").hidden = false;
    if ($("#empty")) $("#empty").hidden = true;
    const it = r.intake || {};
    $("#translation").textContent = it.translation_en ? `English (${r.modes && r.modes.intake === "gemini" ? "translated by Gemini" : "stored translation"}): ${it.translation_en}` : "";
    $("#cap-defect").textContent = it.photo_observation || "Defect photo";
    const box = $("#box");
    if (it.box_2d && c.photo) {
      const [y0, x0, y1, x1] = it.box_2d;
      Object.assign(box.style, { top: y0 / 10 + "%", left: x0 / 10 + "%", height: (y1 - y0) / 10 + "%", width: (x1 - x0) / 10 + "%" });
      box.hidden = false;
      box.classList.toggle("ok", state.boxOk);
      $("#box-ok").hidden = false;
      $("#box-note").textContent = state.boxOk ? "Box confirmed by you."
        : (r.modes && r.modes.intake === "gemini" ? "Gemini proposed this box." : "This box was saved with the example.") + " Check that it covers the defect, then confirm it.";
    } else { box.hidden = true; $("#box-ok").hidden = true; $("#box-note").textContent = c.photo ? "No defect box proposed." : ""; }
    const d2 = it.d2 || {};
    $("#w5").innerHTML = ["what", "where", "when", "how_many"].map((k) => d2[k] ? `<dt>${esc(k.replace("_", " "))}</dt><dd>${esc(d2[k])}</dd>` : "").join("");

    renderIsNot(r.analysis);
    renderVerdict(r);
    renderReport(r);
    renderHyps(r.hypotheses || []);
    renderD4(r.draft, r);
    renderD3(r.containment, r.draft);
    renderRules(r.challenger || []);
    renderTries(r);
    const acts = r.actions || [];
    $("#d5").innerHTML = acts.length ? `<ul class="acts">${acts.map((a) => `<li>${esc(a.action)}<br><span class="who">${esc(a.owner)} · due ${esc(a.due)}</span></li>`).join("")}</ul>`
      : `<p class="small">${esc((r.report && r.report.en && r.report.en.D5) || "")}</p>`;
    renderSign(approvals || {});
    renderEvidence(r.evidence || []);
    renderTrace(r);
    const m = r.modes || {};
    $("#provenance").textContent = `Data: ${r.data_source}. Intake: ${m.intake}. Investigation: ${m.investigation}. Writer: ${m.writer}.` +
      (r.model ? ` Model: ${r.model}.` : "") + (r.usage && r.usage.calls ? ` Gemini calls: ${r.usage.calls}, tokens in/out: ${r.usage.input_tokens}/${r.usage.output_tokens}.` : "") +
      ` Run ${String(r.created_at).slice(0, 16).replace("T", " ")} UTC in ${r.duration_s}s.`;
    const notes = r.notes || [];
    $("#notes").hidden = !notes.length;
    $("#notes").textContent = notes.join(" ");
  }

  function renderIsNot(a) {
    if (!a) { $("#heatmap").innerHTML = ""; return; }
    const hm = a.heatmap;
    const cw = 52, ch = 26, left = 52, top = 22;
    const W = left + hm.cols.length * cw + 4, H = top + hm.rows.length * ch + 4;
    let s = `<svg class="hm" viewBox="0 0 ${W} ${H}" role="img" aria-label="Defect rate by ${esc(hm.row_dim)} and ${esc(hm.col_dim)} in the complaint window">`;
    s += `<text x="4" y="14" font-size="9.5" fill="var(--ink-3)">% bad</text>`;
    hm.cols.forEach((col, j) => { s += `<text x="${left + j * cw + cw / 2}" y="14" text-anchor="middle" font-size="10.5" fill="var(--ink-3)">${esc(col)}</text>`; });
    hm.rows.forEach((row, i) => {
      s += `<text x="${left - 6}" y="${top + i * ch + 17}" text-anchor="end" font-size="10.5" fill="var(--ink-3)">${esc(row)}</text>`;
      hm.cells[i].forEach((cell, j) => {
        const ratio = cell.rate != null && hm.baseline_rate ? cell.rate / hm.baseline_rate : 0;
        const fill = cell.rate == null ? "var(--code)" : ratio >= 5 ? "var(--heat4)" : ratio >= 2.5 ? "var(--heat2)" : ratio >= 1.5 ? "var(--heat1)" : "var(--heat0)";
        const tcol = ratio >= 5 ? "#fff" : "var(--ink)";
        s += `<rect x="${left + j * cw}" y="${top + i * ch}" width="${cw - 2}" height="${ch - 2}" fill="${fill}"/>`;
        s += `<text x="${left + j * cw + cw / 2 - 1}" y="${top + i * ch + 16}" text-anchor="middle" font-size="10.5" fill="${tcol}">${cell.rate == null ? "–" : (cell.rate * 100).toFixed(1)}</text>`;
      });
    });
    s += "</svg>";
    $("#heatmap").innerHTML = s;
    const o = a.overall;
    $("#isnot-h").textContent = a.significant ? `IS: ${a.primary_label}` : "No segment stands out";
    const shiftNote = hm.col_dim === "shift" || hm.row_dim === "shift" ? " Shifts: D day 06:00 to 14:00, S swing 14:00 to 22:00, N night 22:00 to 06:00." : "";
    renderIsNotTable(a);
    $("#isnot-text").textContent = `${a.defect} rate ${pct(o.rate_test)} in the complaint window vs ${pct(o.rate_control)} in the 28 days before (×${o.ratio}). Cells: % defective by ${hm.row_dim} × ${hm.col_dim}, complaint window.${shiftNote}`;
    $("#isnot-method").textContent = "Method: " + a.method + (a.bqml_error ? " (BigQuery ML unavailable for this run)" : "");
    const bq = (a.bqml_insights || []).filter((x) => x.segment !== "all").slice(0, 4);
    if (bq.length) {
      const fmt = (x) => (x == null ? "–" : Number(x).toFixed(2));
      $("#isnot-method").insertAdjacentHTML("beforeend", `<br><b>BigQuery ML top contributors:</b> ` +
        bq.map((x) => `${esc(x.segment)} (test/control ×${fmt(x.test_over_control)}, support ${fmt(x.apriori_support)})`).join("; "));
      const prim = a.primary_segment || {};
      const agrees = Object.entries(prim).every(([k, v]) => String(bq[0].segment).includes(`${k}=${v}`) || String(bq[0].segment).includes(v));
      if (a.significant && agrees) $("#isnot-method").insertAdjacentHTML("beforeend", `<br><span class="bqok">✓ BigQuery ML agrees: ${esc(bq[0].segment)}</span>`);
    }
  }

  // The classic is / is not worksheet, filled from the analysis: where and when the defect is, and where and when it isn't.
  function renderIsNotTable(a) {
    const el = $("#isnot-table"), hm = a.heatmap, o = a.overall, prim = a.primary_segment || {};
    const win = (w) => (w && w.length === 2 ? `${w[0]} → ${w[1]}` : "");
    let rows = `<tr><th>When</th><td class="is">date codes ${esc(win(a.test_window))}: ${pct(o.rate_test)}</td><td>the 28 days before: ${pct(o.rate_control)}</td></tr>`;
    if (a.significant && Object.keys(prim).length) {
      const seg = (a.segments || []).find((x) => x.label === a.primary_label);
      const inPrim = (dim, v) => !(dim in prim) || String(prim[dim]) === String(v);
      const rest = [];
      hm.rows.forEach((row, i) => hm.cols.forEach((col, j) => {
        const cell = hm.cells[i][j];
        if (cell.rate != null && !(inPrim(hm.row_dim, row) && inPrim(hm.col_dim, col))) rest.push(cell.rate);
      }));
      const lo = Math.min(...rest), hi = Math.max(...rest);
      const restTxt = rest.length ? `everywhere else: ${(lo * 100).toFixed(1)}% to ${(hi * 100).toFixed(1)}%` : "everywhere else";
      rows = `<tr><th>Where</th><td class="is">${esc(a.primary_label)}${seg ? ": " + pct(seg.rate_test) : ""}</td><td>${esc(restTxt)}</td></tr>` + rows;
    }
    el.innerHTML = `<table class="isnot"><thead><tr><th></th><th>Is</th><th>Is not</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function renderHyps(h) {
    const ul = $("#hyps");
    if (!h.length) { ul.innerHTML = `<li class="small muted">No hypothesis passed the significance test.</li>`; return; }
    const cls = { SUPPORTED: "ok", REJECTED: "", EXPLAINED: "neutral", OPEN: "warn" };
    const order = { SUPPORTED: 0, REJECTED: 1, OPEN: 2, EXPLAINED: 3 };
    const main = h.filter((x) => x.ruling !== "EXPLAINED").sort((a, b) => order[a.ruling] - order[b.ruling]);
    const expl = h.filter((x) => x.ruling === "EXPLAINED");
    ul.innerHTML = main.map((x) => `<li><div class="h"><b>${esc(x.hypothesis)}</b><span class="stamp ${cls[x.ruling] || ""}">${esc(x.ruling)}</span></div>
      <span class="small muted">looks ${esc(x.looks)}</span><span class="small">${esc(x.reason).replace(/^confounded/, term("confounded", "confounded"))}</span>
      <span class="chips">${(x.evidence || []).filter(Boolean).map(chip).join("")}</span></li>`).join("") +
      (expl.length ? `<li class="explained"><b>${expl.length} side effects explained:</b> ${esc(expl.map((x) => x.hypothesis).join(", "))} look elevated only because they include the primary segment.</li>` : "");
  }

  function renderPlain(r) {
    const el = $("#plain"), c = (state.data && state.data.case) || {};
    if (!r) { el.hidden = true; return; }
    const what = esc(String(c.defect || "defect").replace("_", " "));
    const lots = r.containment && r.containment.targeted_lots;
    el.innerHTML = (r.draft && r.draft.insufficient_evidence
      ? `A customer reported a ${what} problem. The plant data shows no cause, so the agents say so instead of guessing.`
      : `A customer reported a ${what} problem. The agents found why it happened, why inspection missed it` +
        (lots ? `, and which ${lots} lots to hold.` : ".")) +
      ` Every claim links to its evidence. <a href="#c-rules">Try to break it ↓</a> or <button class="link inline" type="button" data-tour>take the 2-minute tour</button>.`;
    el.hidden = false;
  }

  function renderVerdict(r) {
    renderPlain(r);
    const el = $("#verdict");
    if (!r) { el.innerHTML = ""; return; }
    const d = r.draft || {}, last = (r.challenger || []).slice(-1)[0] || { rules: [] };
    const passed = last.rules.filter((x) => x.passed).length;
    const card = (k, v, cls = "") => `<div class="vcard ${cls}"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`;
    if (d.insufficient_evidence) {
      el.innerHTML = card("Root cause", "Insufficient evidence: none claimed", "green") +
        card("Why", "No press, cavity, shift, crew or resin lot stands out") +
        card("Containment", "No blanket hold; customer sorts stock") +
        card("Challenger checks", `${passed}/${last.rules.length} passed (no signal, so the cause checks don't apply)`, "green");
      return;
    }
    const rejected = (r.hypotheses || []).filter((x) => x.ruling === "REJECTED" && /^(crew|shift)/.test(x.hypothesis));
    const c = r.containment;
    const label = (r.analysis && r.analysis.primary_label) || "";
    const clause = firstClause(d.occurrence && d.occurrence.statement);
    el.innerHTML = card("Root cause", label && !clause.toLowerCase().includes(label.toLowerCase()) ? `${label}: ${clause}` : clause) +
      card("People blamed", rejected.length ? `None. ${rejected.map((x) => x.hypothesis).join(", ")} looked worse but were rejected after splitting by press` : "None", "green") +
      card("Containment", c ? `Hold ${c.targeted_lots} lots, not ${c.blanket_lots}` + (c.blanket_parts ? ` · ${Math.round(100 - (100 * c.targeted_parts) / c.blanket_parts)}% fewer parts held` : "") : "–", "green") +
      card("Challenger checks", `${passed}/${last.rules.length} passed` + ((r.challenger || []).length > 1 ? ` after ${(r.challenger || []).length} rounds` : ""), last.verdict === "REJECTED" ? "red" : "green");
  }

  function firstClause(s) {
    s = String(s || "").replace(/^Occurrence:\s*/i, "");
    const cut = s.split(/;|: |, so |, which | \(/)[0];  // ": " so times such as 22:00 are not cut
    return cut.length > 90 ? cut.slice(0, 88) + "…" : cut;
  }

  let reportLang = "en";
  function renderReport(r) {
    const el = $("#report");
    const sec = r && r.report && (r.report[reportLang] || r.report.en);
    if (!sec) { el.innerHTML = ""; return; }
    const names = reportLang === "ja"
      ? { D1: "D1 チーム", D2: "D2 問題", D3: "D3 暫定処置", D4: "D4 発生原因", D4_escape: "D4 流出原因", D5: "D5 是正処置", D6: "D6 効果確認", D7: "D7 再発防止", D8: "D8 称賛" }
      : { D1: "D1 Team", D2: "D2 Problem", D3: "D3 Containment", D4: "D4 Occurrence", D4_escape: "D4 Escape", D5: "D5 Actions", D6: "D6 Verify", D7: "D7 Prevent", D8: "D8 Close" };
    el.className = "report " + reportLang;
    el.innerHTML = Object.keys(names).filter((k) => sec[k]).map((k) => `<dt>${esc(names[k])}</dt><dd>${esc(sec[k])}</dd>`).join("");
  }
  function setLang(l) {
    reportLang = l;
    $("#lang-en").className = "btn small" + (l === "en" ? "" : " ghost"); $("#lang-en").setAttribute("aria-pressed", String(l === "en"));
    $("#lang-ja").className = "btn small" + (l === "ja" ? "" : " ghost"); $("#lang-ja").setAttribute("aria-pressed", String(l === "ja"));
    renderReport(state.data && state.data.result);
  }
  $("#lang-en").addEventListener("click", () => setLang("en"));
  $("#lang-ja").addEventListener("click", () => setLang("ja"));

  function chip(id) { return `<button type="button" class="chip" data-ev="${esc(id)}">${esc(id)}</button>`; }

  function renderD4(d, r) {
    const el = $("#d4");
    if (!d) { el.innerHTML = ""; return; }
    if (d.insufficient_evidence) {
      el.innerHTML = `<p><span class="stamp warn">Insufficient evidence</span></p><p class="small">${esc(d.summary || "")}</p>
        <p class="small"><b>Collect next:</b></p><ul class="small">${(d.next_data || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
      return;
    }
    const o = d.occurrence || {}, e = d.escape || {};
    el.innerHTML = `<div class="cause"><b class="k">${term("occurrence", "Occurrence")}</b><span>${esc(o.statement)}</span><span class="chips">${(o.evidence || []).map(chip).join("")}</span></div>
      <div class="cause esc"><b class="k">${term("escape", "Escape")}</b><span>${esc(e.statement || "Not found yet")}</span><span class="chips">${(e.evidence || []).map(chip).join("")}</span></div>` +
      ((d.open_points || []).length ? `<p class="openpts"><b>Open before closing D4:</b> ${esc(d.open_points.join("; "))}. The cause is located; the draft says what it can't yet prove instead of guessing.</p>` : "");
  }

  function renderD3(c, d) {
    const el = $("#d3");
    if (!c) { el.innerHTML = d && d.insufficient_evidence ? `<p class="small">No lot concentration found, so no blanket hold. Ask the customer to sort the stock they have on hand.</p>` : ""; return; }
    const st = c.by_status || {};
    const w = (n) => Math.max(2, Math.round((n / Math.max(c.blanket_lots, 1)) * 100));
    const parts = c.targeted_parts ? `<p class="small">≈ ${Number(c.targeted_parts).toLocaleString()} parts held instead of ${Number(c.blanket_parts).toLocaleString()}.</p>` : "";
    el.innerHTML = `<p><span class="big">${c.targeted_lots} lots</span> <span class="muted">not ${c.blanket_lots}</span></p>${parts}
      <div class="bars"><div class="bar"><span>targeted</span><i class="hold" style="width:${w(c.targeted_lots)}%"></i><span>${c.targeted_lots}</span></div>
      <div class="bar"><span>blanket</span><i style="width:100%"></i><span>${c.blanket_lots}</span></div></div>
      <p class="small">${esc(c.segment_label)}, made ${esc(c.suspect_window[0])} → ${esc(c.suspect_window[1])}: ${st.at_customer || 0} at customer, ${st.in_transit || 0} in transit, ${st.in_plant || 0} in plant.${c.note ? " " + esc(c.note) + "." : ""}</p>
      <details><summary class="small">Lot list (${c.lots.length})</summary><div class="lots-wrap"><table class="lots"><thead><tr><th>Lot</th><th>Made</th><th>Shipment</th><th>Status</th></tr></thead><tbody>
      ${c.lots.map((l) => `<tr><td>${esc(l.lot_id)}</td><td>${esc(l.prod_date)}</td><td>${esc(l.shipment_id)}</td><td>${esc(l.status.replace("_", " "))}</td></tr>`).join("")}
      </tbody></table></div></details>`;
  }

  function renderRules(rounds) {
    const by = (b) => (b === "deterministic" ? "the rule engine" : /gemini/.test(b) ? "Gemini" : b);
    $("#rules").innerHTML = rounds.map((rd) => `<div class="round">Round ${rd.round} · drafted by ${esc(by(rd.by))} · <span class="stamp ${rd.verdict === "REJECTED" ? "" : "ok"}">${rd.verdict === "REJECTED" ? "Rejected" : "Passed"}</span></div>` +
      rd.rules.map((x) => `<div class="rule"><span class="ic ${x.passed ? "ok" : "no"}">${x.passed ? "✓" : "✗"}</span><span><b>${esc(ruleName(x.rule))}</b> · ${esc(x.message)}</span></div>`).join("")).join("");
  }

  function renderSign(appr) {
    const names = { D2: "D2 problem", D3: "D3 containment", D4: "D4 root causes", D5: "D5 actions" };
    $("#signer").value = signer;
    $("#sign-list").innerHTML = Object.keys(names).map((k) => {
      const a = appr[k];
      return `<li class="${a ? "done" : ""}"><b>${names[k]}</b>${a ? `<span class="small">✓ ${esc(a.by.replace("human:", ""))}, ${esc(a.at.slice(11, 16))} UTC</span>` : `<button type="button" class="btn small" data-approve="${k}">Approve</button>`}</li>`;
    }).join("");
    const all = ["D2", "D3", "D4", "D5"].every((k) => appr[k]);
    $("#exp-en").disabled = $("#exp-ja").disabled = !all;
    $("#exp-en").title = $("#exp-ja").title = all ? "" : "Approve D2 to D5 first";
    renderRail();
  }

  function renderTrace(r) {
    const m = r.modes || {}, model = r.model || "Gemini";
    const fmt = (t) => `${esc(t.tool)}(${esc(Object.entries(t.args || {}).map(([k, v]) => k + "=" + (Array.isArray(v) ? v.join("|") : v)).join(", "))})`;
    const steps = [];
    const hasPhoto = !!(state.data && state.data.case && state.data.case.photo);
    steps.push(m.intake === "gemini" ? `<b>Intake · ${esc(model)}</b> read the complaint${r.intake && r.intake.translation_en ? " (translated it to English)" : ""}` +
        (hasPhoto ? " and compared the defect photo with the golden sample." : ".")
      : `<b>Intake</b> · stored reading (deterministic mode).`);
    const calls = (r.trace || []).filter((t) => t.stage === "investigate");
    if (calls.length) steps.push(`<b>Investigator · ${esc(model)} via ADK</b> chose ${calls.length} tool calls: ${calls.map(fmt).join(" → ")}.`);
    else steps.push(`<b>Investigator</b> · scripted policy (deterministic mode): ${esc((r.evidence || []).map((e) => e.tool).filter((t, i, a) => a.indexOf(t) === i).join(" → "))}.`);
    (r.challenger || []).forEach((rd, i) => {
      const bad = (rd.rules || []).filter((x) => !x.passed).map((x) => ruleName(x.rule).toLowerCase());
      steps.push(`<b>Challenger · round ${rd.round}</b>: ${rd.verdict === "APPROVED_FOR_REVIEW" ? "approved for review" : "rejected"} (${(rd.rules || []).length - bad.length}/${(rd.rules || []).length} rules)${bad.length ? ": " + esc(bad.join(", ")) : ""}.`);
      const rev = (r.trace || []).filter((t) => t.stage === `revise after round ${rd.round}`);
      if (i < (r.challenger || []).length - 1) steps.push(rd.by === "deterministic" || m.investigation === "gemini+scripted" && !rev.length
        ? `<b>Revision</b> · the scripted policy rewrote the draft.` : `<b>Revision · ${esc(model)}</b> fixed the draft${rev.length ? " after " + rev.map(fmt).join(" → ") : ""}.`);
    });
    steps.push(m.writer === "gemini" ? `<b>Writer · ${esc(model)}</b> drafted the 8D in English and Japanese; every percentage and lot count was checked against the evidence.`
      : `<b>Writer</b> · template text${m.intake === "gemini" ? " (the Writer's numbers failed the check or it timed out)" : " (deterministic mode)"}.`);
    $("#trace-list").innerHTML = steps.map((x) => `<li>${x}</li>`).join("");
  }

  function renderEvidence(items) {
    $("#evidence").innerHTML = items.map((i) => `<li id="ev-${esc(i.id)}"><b>${esc(i.id)}</b> ${esc(i.summary)} <span class="src">(${esc(i.source)})</span></li>`).join("");
  }

  // ------------------------------------------------------------------------------------------ actions
  document.addEventListener("click", async (e) => {
    const tr = e.target.closest("[data-try]");
    if (tr) {
      const t = tries[Number(tr.dataset.try)];
      if (!t) return;
      document.querySelectorAll(".try").forEach((b) => b.classList.toggle("on", b === tr));
      $("#bi-occ").value = t.occ; $("#bi-esc").value = t.esc;
      $("#breakit").requestSubmit();
      return;
    }
    if (e.target.closest("[data-tour]")) { startTour(); return; }
    const open = e.target.closest("[data-open]");
    if (open) { $("#how-dlg").close(); selectCase(open.dataset.open); return; }
    const jump = e.target.closest("[data-jump]");
    if (jump) {
      const t = document.getElementById(jump.dataset.jump);
      if (t) { e.preventDefault(); t.scrollIntoView({ behavior: motion(), block: "start" }); }
      return;
    }
    const evBtn = e.target.closest("[data-ev]");
    if (evBtn) {
      const li = document.getElementById("ev-" + evBtn.dataset.ev);
      if (li) { li.scrollIntoView({ block: "center", behavior: "smooth" }); li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 1600); }
      return;
    }
    const ap = e.target.closest("[data-approve]");
    if (ap) {
      signer = $("#signer").value.trim();
      if (signer.length < 2) { $("#signer").focus(); $("#signer").placeholder = "Type your name first"; return; }
      try { localStorage.setItem("rae-signer", signer); } catch (err) {}
      ap.disabled = true;
      const caseId = state.current;
      try {
        const got = await api(`/api/cases/${caseId}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ section: ap.dataset.approve, name: signer, result_at: state.data.result && state.data.result.created_at }) });
        if (caseId !== state.current) return;
        state.data.approvals = { ...(state.data.approvals || {}), ...got };  // responses can arrive out of order; sign-offs only accumulate
        renderSign(state.data.approvals);
      } catch (err) { ap.disabled = false; alertNote(err.message); }
    }
  });

  $("#box-ok").addEventListener("click", () => { state.boxOk = true; render(); });
  $("#reset-btn").addEventListener("click", async () => { await api(`/api/cases/${state.current}/reset`, { method: "POST" }); state.data.approvals = {}; renderSign({}); });
  $("#exp-en").addEventListener("click", () => window.open(`/api/cases/${state.current}/report?lang=en&v=${encodeURIComponent(visitor)}`, "_blank", "noopener"));
  $("#exp-ja").addEventListener("click", () => window.open(`/api/cases/${state.current}/report?lang=ja&v=${encodeURIComponent(visitor)}`, "_blank", "noopener"));

  function alertNote(msg) { $("#notes").hidden = false; $("#notes").textContent = msg; }

  // live run (Server-Sent Events)
  $("#run-btn").addEventListener("click", () => runLive(state.current));
  function runLive(id) {
    if (!id) return;  // no case loaded yet
    if (state.live) state.live.close();
    const tr = $("#tracker"), feed = $("#feed");
    tr.hidden = false; feed.innerHTML = "";
    window.scrollTo({ top: 0, behavior: "smooth" });
    document.querySelectorAll(".stages li").forEach((li) => li.className = "");
    $("#run-btn").disabled = true;
    $("#run-note").textContent = "Running…";
    const add = (html) => { const li = document.createElement("li"); li.innerHTML = html; feed.appendChild(li); feed.scrollTop = feed.scrollHeight; };
    const mark = (stage) => {
      let past = true;
      document.querySelectorAll(".stages li").forEach((li) => {
        if (li.dataset.stage === stage) { li.className = "run"; past = false; } else if (past) li.className = "done";
      });
    };
    const es = new EventSource(`/api/cases/${encodeURIComponent(id)}/live?v=${encodeURIComponent(visitor)}`);
    state.live = es; state.liveId = id;
    let ended = false;
    const finish = (note) => {
      if (ended) return;
      ended = true; es.close();
      if (state.live === es) { state.live = null; $("#run-btn").disabled = false; $("#run-note").textContent = note || ""; }
      loadMeta();
    };
    es.onmessage = (m) => {
      let e; try { e = JSON.parse(m.data); } catch (err) { return; }
      if (state.live !== es) { es.close(); return; }  // a newer run or another case took over
      if (e.type === "quota") { if (e.message) add(esc(e.message)); }  // informational: the run continues
      else if (e.type === "limit") { add(esc(e.message)); finish("Limit reached"); }
      else if (e.type === "stage") { mark(e.stage); add(`<b>${esc(e.stage)}</b> ${esc(e.label || "")}`); }
      else if (e.type === "intake") add(`<b>intake</b> ${esc(e.intake.photo_observation || "")}`);
      else if (e.type === "call") add(`<b>Gemini →</b> ${esc(e.name)}(${esc(Object.entries(e.args || {}).map(([k, v]) => k + "=" + (Array.isArray(v) ? v.join("|") : v)).join(", "))})`);
      else if (e.type === "evidence") add(`<b>${esc(e.item.id)}</b> ${esc(e.item.summary)}`);
      else if (e.type === "challenger") { mark("challenger"); add(`<b>challenger</b> round ${e.round.round}: ${esc(e.round.verdict)}${e.round.verdict === "REJECTED" ? " · " + esc(e.round.rules.filter((x) => !x.passed).map((x) => ruleName(x.rule)).join(", ")) : ""}`); }
      else if (e.type === "done") {
        document.querySelectorAll(".stages li").forEach((li) => li.className = "done");
        if (state.current === id) { state.data.result = e.result; state.data.approvals = {}; render(); }  // a new run needs new signatures
        finish(`Done in ${e.result.duration_s}s`);
        loadCases(null, false);  // refresh the list's tags without pulling the visitor back to the case that just ran
      } else if (e.type === "error") { add(esc(e.message)); finish("Stopped"); }
    };
    es.onerror = () => finish("Connection closed. The stored result is still shown.");
  }

  // new case
  $("#new-btn").addEventListener("click", () => { $("#new-err").textContent = ""; $("#new-dlg").showModal(); });
  $("#new-cancel").addEventListener("click", () => $("#new-dlg").close());
  $("#f-example").addEventListener("click", () => {
    $("#f-title").value = "Clips cracking at the hook, second customer";
    $("#f-complaint").value = "Hello,\n\nOur incoming inspection found cracks at the hook root on CL-204 snap clips: 12 of 1,200 pieces in the delivery we received on 10 Oct. " +
      "Date codes 2026-10-05 to 2026-10-10. Please contain the stock you have in the plant and in transit, and send us an 8D.\n\nRegards,\nIncoming Quality, OEM-K";
    $("#f-defect").value = "crack"; $("#f-start").value = "2026-10-05"; $("#f-end").value = "2026-10-10";
  });
  $("#new-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData($("#new-form"));
    if (!$("#f-photo").files.length) fd.delete("photo");
    $("#new-submit").disabled = true;
    try {
      const r = await api("/api/cases", { method: "POST", body: fd });
      $("#new-dlg").close();
      await loadCases(r.case_id);
      runLive(r.case_id);
    } catch (err) { $("#new-err").textContent = err.message; }
    finally { $("#new-submit").disabled = false; }
  });

  // how it works + benchmark
  $("#how-btn").addEventListener("click", async () => {
    $("#how-dlg").showModal();
    try {
      const b = await api("/api/benchmark");
      const miss = b.caused_total - b.caused_found;
      $("#bench").textContent = `How well it works, on synthetic test plants: it found the planted cause in ${b.caused_found} of ${b.caused_total}` +
        (miss ? ` (${miss === 1 ? "the miss" : "the misses"} said "insufficient evidence" rather than guess)` : "") +
        `, claimed no cause on ${b.controls_abstained} of ${b.controls_total} plants that had none, and the hold caught ${Math.round(100 * b.mean_containment_recall)}% of the affected lots. ` +
        `End to end on 31 test plants, every 8D passed the Challenger with no invented number or mechanism. In a held-out blind red-team round, 89% of never-seen claims were judged correctly, and every one of the 451 tricky claims found so far is now a test.`;
    } catch (err) { $("#bench").textContent = "Benchmark unavailable right now."; }
  });
  $("#how-close").addEventListener("click", () => $("#how-dlg").close());
  $("#how-tour").addEventListener("click", () => { $("#how-dlg").close(); startTour(); });
  $("#tour-btn").addEventListener("click", () => startTour());

  // ------------------------------------------------------------------------------------------ glossary
  const gloss = $("#gloss");
  function showGloss(el) {
    const g = GLOSS[el.dataset.term];
    if (!g) return;
    gloss.innerHTML = `<b>${esc(g[0])}</b>${esc(g[1])}`;
    gloss.hidden = false;
    const r = el.getBoundingClientRect(), w = gloss.offsetWidth, h = gloss.offsetHeight;
    const left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    const top = r.bottom + 6 + h > window.innerHeight ? r.top - h - 6 : r.bottom + 6;
    Object.assign(gloss.style, { left: left + "px", top: Math.max(8, top) + "px" });
    gloss.dataset.for = el.dataset.term;
    gloss.shownAt = Date.now();
  }
  const hideGloss = () => { gloss.hidden = true; gloss.dataset.for = ""; };
  document.addEventListener("mouseover", (e) => { const t = e.target.closest(".term"); if (t) showGloss(t); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest(".term")) hideGloss(); });
  document.addEventListener("focusin", (e) => { const t = e.target.closest(".term"); if (t) showGloss(t); });
  document.addEventListener("focusout", (e) => { if (e.target.closest(".term")) hideGloss(); });
  document.addEventListener("click", (e) => {  // touch screens have no hover: a tap toggles it
    const t = e.target.closest(".term");
    if (!t) return;
    if (gloss.dataset.for === t.dataset.term && !gloss.hidden) { if (Date.now() - (gloss.shownAt || 0) > 500) hideGloss(); }
    else showGloss(t);
  });
  window.addEventListener("scroll", hideGloss, { passive: true });

  // ------------------------------------------------------------------------------------------ guided tour
  const TOUR = [
    ["c-d2", "The complaint", (r) => `A car maker reports ${esc((r.intake && r.intake.d2 && r.intake.d2.how_many) || "bad parts")}. The email is translated, and the defect is boxed on the photo next to a known-good part.`],
    ["c-d3", "Hold only what's suspect", (r) => { const c = r.containment; return c ? `The plant records show which lots could be affected: ${c.targeted_lots} lots, not all ${c.blanket_lots}. The customer's line keeps running and the suspect parts are still caught.` : "No lots stand out, so there's no blanket hold."; }],
    ["c-isnot", "Where it is, and where it isn't", (r) => { const a = r.analysis || {}; return a.significant ? `Defect rate by ${esc(a.heatmap.row_dim)} and ${esc(a.heatmap.col_dim)} for the complaint's date codes. ${esc(cap(a.primary_label))} stands out; the rest stay near their normal rate.` : "Nothing stands out in the data, and the page says so."; }],
    ["c-hyp", "Every suspect, tested", (r) => { const h = (r.hypotheses || []).find((x) => x.ruling === "REJECTED" && /^crew/.test(x.hypothesis)); return h ? `${esc(cap(h.hypothesis))}'s defect rate looks high: ${esc(h.looks)}. Split by press, the gap disappears: ${esc(h.hypothesis)} simply works the faulty press more often, so nobody gets blamed.` : "Each suspect is tested against the data and marked supported or rejected, with the evidence."; }],
    ["c-d4", "Two root causes", () => "Why it happened, and why inspection didn't catch it. An 8D needs both. Click any E-number to see the record behind a claim."],
    ["c-rules", "Try to break it", () => "Pick an example such as blaming the crew, or write your own claim. The checks that reviewed the agents' draft judge yours the same way."],
    ["c-sign", "Nothing leaves unsigned", () => "A quality engineer approves D2 to D5. Only then can the 8D be exported, in English or Japanese. To watch the agents do all of this from scratch, press Run live."],
  ];
  let tourAt = -1;
  async function startTour() {
    if (state.current !== "118" && state.cases.some((c) => c.case_id === "118")) await selectCase("118");
    if (!state.data || !state.data.result) return;
    try { localStorage.setItem("rae-toured", "1"); } catch (e) {}
    $("#board").classList.add("touring");
    $("#coach").hidden = false;
    showStep(0);
  }
  function showStep(i) {
    tourAt = i;
    const [id, title, text] = TOUR[i];
    document.querySelectorAll(".cell.spot").forEach((x) => x.classList.remove("spot"));
    const el = document.getElementById(id);
    el.classList.add("spot");
    el.scrollIntoView({ behavior: motion(), block: "start" });
    $("#coach-n").textContent = `Step ${i + 1} of ${TOUR.length} · case 118`;
    $("#coach-h").textContent = title;
    $("#coach-p").innerHTML = text(state.data.result);
    $("#coach-back").disabled = i === 0;
    $("#coach-next").textContent = i === TOUR.length - 1 ? "Finish" : "Next";
  }
  function endTour() {
    tourAt = -1;
    $("#coach").hidden = true;
    $("#board").classList.remove("touring");
    document.querySelectorAll(".cell.spot").forEach((x) => x.classList.remove("spot"));
  }
  $("#coach-next").addEventListener("click", () => (tourAt >= TOUR.length - 1 ? endTour() : showStep(tourAt + 1)));
  $("#coach-back").addEventListener("click", () => tourAt > 0 && showStep(tourAt - 1));
  $("#coach-x").addEventListener("click", endTour);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { hideGloss(); if (tourAt >= 0) endTour(); } });

  // D1 to D8 across the top, the way an 8D form reads: status, and the customer's due date for D3, D4 and D5.
  function renderRail() {
    const el = $("#rail");
    if (!state.data) { el.innerHTML = ""; return; }
    const { case: c, result: r } = state.data, appr = state.data.approvals || {};
    const rec = new Date(c.received), now = new Date();
    const demo = !String(c.case_id).startsWith("U-");  // demo cases have fixed dates; don't let them turn "overdue" while judges look
    const due = (days) => {
      if (isNaN(rec)) return "";
      const d = new Date(rec.getTime() + days * 864e5), h = (d - now) / 36e5;
      if (demo) return `<span class="due">due ${esc(d.toISOString().slice(0, 10))}</span>`;
      const cls = h < 0 ? "over" : h < 24 ? "soon" : "";
      return `<span class="due ${cls}">${h < 0 ? "overdue" : h < 48 ? `due in ${Math.max(1, Math.round(h))} h` : "due " + esc(d.toISOString().slice(0, 10))}</span>`;
    };
    const dr = (r && r.draft) || {};
    const signed = (k) => appr[k] && { cls: "signed", s: "Signed · " + appr[k].by.replace("human:", "") };
    const drafted = { cls: "drafted", s: "Awaiting signature" };
    const st = {
      D1: { cls: "drafted", s: "In the draft" },
      D2: signed("D2") || drafted,
      D3: signed("D3") || (dr.insufficient_evidence ? { cls: "open", s: "Customer sort" } : drafted),
      D4: signed("D4") || (dr.insufficient_evidence ? { cls: "open", s: "No cause claimed" } : (dr.open_points || []).length ? { cls: "open", s: "Open points" } : drafted),
      D5: signed("D5") || drafted,
      D6: { cls: "drafted", s: "In the draft" }, D7: { cls: "drafted", s: "In the draft" },
      D8: ["D2", "D3", "D4", "D5"].every((k) => appr[k]) ? { cls: "signed", s: "Ready to export" } : { cls: "", s: "After sign-off" },
    };
    const steps = [["D1", "Team", "c-report"], ["D2", "Problem", "c-d2", 0], ["D3", "Containment", "c-d3", 1], ["D4", "Root causes", "c-d4", 5],
      ["D5", "Actions", "c-d5", 20], ["D6", "Verify", "c-report"], ["D7", "Prevent", "c-report"], ["D8", "Close", "c-sign"]];
    el.innerHTML = steps.map(([k, name, target, days]) => {
      const x = r ? st[k] : { cls: "", s: "Not run yet" };
      return `<a href="#${target}" class="${x.cls}" data-jump="${target}"><span class="d">${k}</span><span class="t">${esc(name)}</span>` +
        `<span class="s">${esc(x.s)}</span>${days ? due(days) : ""}</a>`;
    }).join("");
  }

  // "Try to break it" examples, built from this case's own draft so they work on any case.
  function buildTries(r) {
    const d = (r && r.draft) || {};
    if (!r) return [];
    if (d.insufficient_evidence) return [
      { label: "Blame the night shift", occ: "Operator error: the night shift did not follow the work instruction. Retrain the night shift.", esc: "Escape: inspection missed it." },
      { label: "Name a cause anyway", occ: "Occurrence: the press P1 mould wore out.", esc: "Escape: the gauge was out of calibration." },
      { label: "Say what the data shows", occ: "Insufficient evidence: no press, cavity, shift, crew or resin lot stands out, so no cause is claimed.", esc: "" },
    ];
    const occ = (d.occurrence && d.occurrence.statement) || "", escTxt = (d.escape && d.escape.statement) || "";
    if (!occ) return [];
    const who = ((r.hypotheses || []).find((h) => h.ruling === "REJECTED" && /^crew/.test(h.hypothesis)) ||
      (r.hypotheses || []).find((h) => h.ruling === "REJECTED" && /^shift/.test(h.hypothesis)) || { hypothesis: "the night crew" }).hypothesis;
    const tries = [{ label: `Blame ${who}`, occ: `Operator error: ${who} did not follow the work instruction. Retrain ${who}.`, esc: escTxt }];
    const press = ((r.analysis || {}).primary_segment || {}).press;
    if (press) {
      const other = press === "P1" ? "P2" : "P1", rx = new RegExp(`\\b${press}\\b`, "g");
      tries.push({ label: "Point at the wrong press", occ: occ.replace(rx, other), esc: escTxt.replace(rx, other) });
    }
    const m = occ.match(/(\d+(?:\.\d+)?)%/);
    tries.push({ label: "Invent a number", esc: escTxt,
      occ: m ? occ.replace(m[0], (parseFloat(m[1]) * 1.5).toFixed(2) + "%") : occ + " The defect rate went up to 7.5%." });
    tries.push({ label: "Leave out the escape", occ, esc: "" });
    tries.push({ label: "The agents' own answer", occ, esc: escTxt });
    return tries;
  }
  let tries = [];
  function renderTries(r) {
    tries = buildTries(r);
    $("#bi-tries").innerHTML = tries.map((t, i) => `<button type="button" class="try" data-try="${i}">${esc(t.label)}</button>`).join("");
  }

  $("#breakit").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#bi-out");
    out.textContent = "Checking…";
    try {
      const r = await api(`/api/cases/${state.current}/challenge`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ occurrence: $("#bi-occ").value, escape: $("#bi-esc").value }) });
      const bad = r.rules.filter((x) => !x.passed);
      out.innerHTML = (r.verdict === "REJECTED" ? `<span class="stamp">Rejected</span>` : `<span class="stamp ok">Passed</span>`) +
        (bad.length ? bad.map((x) => `<div class="rule"><span class="ic no">✗</span><span><b>${esc(ruleName(x.rule))}</b> · ${esc(x.message)}</span></div>`).join("")
          : `<p class="small">All ${r.rules.length} checks pass. A person still has to sign it.</p>`) +
        (r.critic === "gemini" || r.critic === "skipped" ? "" : `<p class="small muted">${{ off: "The Gemini critic is off in this mode, so contradictions and filler are not checked; the code rules above still are.",
          quota: "Today's Gemini critic checks are used up, so contradictions and filler were not checked this time.",
          error: "The Gemini critic did not answer in time, so contradictions and filler were not checked this time." }[r.critic] || ""}</p>`);
    } catch (err) { out.textContent = err.message; }
  });

  window.addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (id && id !== state.current && state.cases.some((c) => c.case_id === id)) selectCase(id);
  });

  loadMeta();
  loadCases();
})();
