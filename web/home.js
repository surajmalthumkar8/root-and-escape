/* Root & Escape: the overview page. The example explorer is drawn from each case's stored run (the same API the
   workbench uses), so what it shows is what the agents produced. Plain JS, no build step. */
(function () {
  "use strict";
  const R = window.RAE;
  const ROOT = document.getElementById("home");
  if (!R || !ROOT) return;
  const $ = (s, el = ROOT) => el.querySelector(s);
  const esc = R.esc;
  const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ counters and the flow, when they come into view
  const seen = new IntersectionObserver((items) => items.forEach((it) => {
    if (!it.isIntersecting) return;
    seen.unobserve(it.target);
    if (it.target.dataset.count != null) countUp(it.target);
    if (it.target.classList.contains("h-flow")) [...it.target.children].forEach((li, i) => setTimeout(() => li.classList.add("on"), still ? 0 : 160 * i));
  }), { threshold: 0.35 });
  function countUp(el) {
    const to = Number(el.dataset.count), of = el.dataset.of, unit = el.dataset.unit || "";
    const put = (v) => { el.textContent = (of ? `${v} / ${of}` : String(v)) + unit; };
    if (still || !to) return put(to);
    const t0 = performance.now(), dur = 900;
    const step = (t) => { const k = Math.min(1, (t - t0) / dur); put(Math.round(to * (1 - Math.pow(1 - k, 3)))); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
  ROOT.querySelectorAll(".h-flow").forEach((el) => seen.observe(el));

  // the tour opens in its own window, big enough to read
  const dlg = document.getElementById("tour-dlg"), vid = document.getElementById("tour-vid");
  if (dlg && vid) {
    document.getElementById("tour-open").addEventListener("click", () => { dlg.showModal(); vid.currentTime = 0; vid.play().catch(() => {}); });
    document.getElementById("tour-close").addEventListener("click", () => dlg.close());
    dlg.addEventListener("close", () => vid.pause());
  }

  // ------------------------------------------------------------------ the example explorer
  const TOOL = {
    is_is_not: "Found where the defects concentrate",
    stratify: "Split a suspect by press to test it",
    param_excursions: "Read the machine logs",
    inspection_changes: "Checked the inspection plan",
    containment: "Worked out which lots to hold",
    past_8d: "Looked up similar past 8Ds",
  };
  const ANSWER = {
    "118": "Not the crew. Press P4's resin dryer ran wet.",
    "124": "One cavity of press P2: its mould ran past the maintenance limit.",
    "121": "Nothing stands out, so no cause is claimed.",
    "131": "Press P8 ran cold, so the thin latch walls stopped filling.",
    "142": "Hold pressure on press P11 went up on 2 Oct, and the mould flashed.",
    "153": "Not the crew. Press P14 ran too hot and the acetal broke down.",
  };
  const GUESS = {
    "118": { q: "37 clips cracked. Who or what would you blame?", opts: ["Crew B, the new night crew", "Press P4's resin dryer", "A bad batch of resin", "A worn mould"], a: 1 },
    "124": { q: "Clips with sharp burrs. What's your guess?", opts: ["The night shift", "One cavity of press P2's mould", "Press P4's dryer", "A bad batch of resin"], a: 1 },
    "121": { q: "A few light scratches. What caused them?", opts: ["Crew A", "Press P1", "A bad batch of resin", "Nothing stands out in the data"], a: 3 },
    "131": { q: "Housings with half-formed latch walls. What's your guess?", opts: ["The new resin lot that arrived that week", "Press P8 running cold", "The night shift", "A worn mould"], a: 1 },
    "142": { q: "Battery covers with flash on the tab. What changed?", opts: ["Crew C", "A bad batch of resin", "Hold pressure on press P11", "Press P10's mould"], a: 2 },
    "153": { q: "Gear teeth are cracking. Who or what would you blame?", opts: ["Crew D, new on press P14", "Press P14 running too hot", "A bad batch of acetal", "The customer's test rig"], a: 1 },
  };
  const FOOT = {
    "118": "A crew looks guilty, and the split by press shows it's a machine. The agents name two causes and hold a ninth of the lots.",
    "124": "The defect sits in one cavity of one press, and a gauge nobody calibrated let it through. Two causes, both cited.",
    "121": "No press, cavity, shift, crew or resin lot stands out, so the agents claim no cause and list what to measure next.",
    "131": "A new resin lot arrived the same week and looks guilty, but it feeds all three HX-310 presses and only P8 went bad. P8's melt temperature log shows why.",
    "142": "Hold pressure on P11 jumped on 2 Oct and is still high, so the hold runs to today. The flash gauge on P11 was also past its calibration date.",
    "153": "Crew D took over two shifts on P14 the week before and looks guilty. Inside P14 every crew cracks gears at the same rate, so the agents rule the crew out.",
  };
  const WORD = { crack: "cracks", burr: "burrs or flash", scratch: "scratches", short_shot: "short shots" };
  const cache = {};
  let current = "118";
  const day = (iso) => (iso ? new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }) : "");
  const short = (t, n) => { t = String(t || "").replace(/^(Occurrence|Escape):\s*/i, ""); return t.length > n ? t.slice(0, n).replace(/\s+\S*$/, "") + " …" : t; };

  async function load(id) {
    if (!cache[id]) cache[id] = await R.api(`/api/cases/${id}`);
    return cache[id];
  }
  function paintIn(d) {
    const c = d.case, r = d.result || {}, it = r.intake || {}, d2 = it.d2 || {}, b = it.box_2d;
    const box = b ? `<span class="ex-box" style="top:${b[0] / 10}%;left:${b[1] / 10}%;height:${(b[2] - b[0]) / 10}%;width:${(b[3] - b[1]) / 10}%"></span>` : "";
    const text = it.translation_en || c.complaint || "";
    $("#ex-in").innerHTML = `<div class="ex-ph"><img src="/static/photos/${esc(c.photo)}" alt="The customer's photo">${box}</div>
      <blockquote>${esc(short(text, 150))}</blockquote>
      <ul class="ex-chips"><li>${esc(c.part_no || "CL-204")}</li><li>${esc(c.customer || "")}</li><li>${esc(WORD[c.defect] || c.defect)}</li>${c.date_code_start ? `<li>date codes ${esc(day(c.date_code_start))} to ${esc(day(c.date_code_end))}</li>` : ""}${d2.how_many ? `<li>${esc(d2.how_many)}</li>` : ""}</ul>
      <p class="ex-by">${r.modes && r.modes.intake === "gemini" ? "Read by Gemini" : "Read by the scripted engine"}</p>`;
  }
  function paintDid(d) {
    const r = d.result || {}, tr = (r.trace || []).filter((t) => t.tool);
    const calls = tr.length ? tr : ["is_is_not", "stratify", "param_excursions", "inspection_changes", "containment", "past_8d"].map((tool) => ({ tool }));
    const arg = (a) => Object.entries(a || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("|") : v}`).join(", ");
    const by = (t) => (t.args && (t.args.by || t.args.press) ? ` (${[t.args.by && "by " + t.args.by, t.args.press].filter(Boolean).join(", ")})` : "");
    $("#ex-did").innerHTML = `<ol class="ex-calls">${calls.slice(0, 8).map((t, i) => `<li style="--i:${i}" title="${esc(t.tool + (t.args ? "(" + arg(t.args) + ")" : ""))}"><b>${esc((TOOL[t.tool] || t.tool) + by(t))}</b><code>${esc(t.tool)}</code></li>`).join("")}</ol>
      <p class="ex-by">${r.modes && r.modes.investigation === "gemini" ? `Gemini chose these ${calls.length} steps itself, through ADK` : "The fixed plan the app uses when Gemini is off"}</p>`;
  }
  function paintFound(d, id, guessed) {
    const r = d.result || {}, dr = r.draft || {}, g = GUESS[id];
    if (guessed == null) {
      $("#ex-found").innerHTML = `<p class="ex-q">${esc(g.q)} <span>Guess first.</span></p>
        <div class="ex-opts">${g.opts.map((o, i) => `<button type="button" data-guess-ex="${i}">${esc(o)}</button>`).join("")}</div>
        <button type="button" class="link small" data-guess-ex="-1">Skip, just show me</button>`;
      return;
    }
    const right = guessed === g.a;
    const verdict = `${guessed < 0 ? "" : `<p class="ex-res ${right ? "ok" : "no"}">${right ? "You got it." : `You picked "${esc(g.opts[guessed])}".`}</p>`}<p class="ex-big">${esc(ANSWER[id] || "")}</p>`;
    // the suspects that really looked guilty (confounded) first, then the ones that simply rose with everything else
    const ruled = (r.hypotheses || []).filter((h) => h.ruling === "REJECTED")
      .sort((a, b) => /confounded/.test(b.reason || "") - /confounded/.test(a.reason || "")).map((h) => h.hypothesis);
    if (dr.insufficient_evidence) {
      $("#ex-found").innerHTML = `${verdict}<p>${esc(short(dr.summary || "No segment of the plant stands out.", 110))}</p>
        <p class="ex-k">Data to collect next</p><ul class="ex-list">${(dr.next_data || []).slice(0, 3).map((x) => `<li>${esc(short(x, 90))}</li>`).join("")}</ul>`;
    } else {
      $("#ex-found").innerHTML = `${verdict}<p class="ex-k">Why it got out</p><p>${esc(short((dr.escape || {}).statement, 100))}</p>
        ${ruled.length ? `<p class="ex-k">Looked guilty, ruled out</p><ul class="ex-chips no">${ruled.slice(0, 4).map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}`;
    }
    $("#ex-found").classList.add("reveal");
  }
  function paintOut(d, id) {
    const r = d.result || {}, c = r.containment || {}, ch = r.challenger || {};
    const rounds = Array.isArray(ch.rounds) ? ch.rounds : Array.isArray(ch) ? ch : [];
    const last = rounds.length ? rounds[rounds.length - 1] : null;
    const rules = last && last.rules ? `${last.rules.filter((x) => x.passed).length} of ${last.rules.length} checks passed` : r.verdict === "APPROVED_FOR_REVIEW" ? "approved for review" : "";
    const hold = c.targeted_lots ? `<p class="ex-k">Lots to hold</p>
      <div class="ex-bar"><span class="t" style="--w:${(100 * c.targeted_lots / c.blanket_lots).toFixed(1)}%">${c.targeted_lots}</span><span class="b">${c.blanket_lots} if you hold everything</span></div>
      <p class="small">${(c.targeted_parts || 0).toLocaleString("en")} parts held instead of ${(c.blanket_parts || 0).toLocaleString("en")}</p>`
      : `<p class="ex-k">Lots to hold</p><p>None yet: with no cause, the agents don't guess a hold.</p>`;
    $("#ex-out").innerHTML = `${hold}<p class="ex-k">The 8D draft</p><p>English and Japanese, waiting for a signature.${rules ? ` Challenger: ${esc(rules)}.` : ""}</p>
      <div class="ex-btns"><button class="btn small" type="button" data-bench="${id}">Open it in the workbench</button><button class="btn small ghost" type="button" data-bench="${id}" data-run="1">Run it live</button></div>`;
  }
  async function show(id) {
    current = id;
    ROOT.querySelectorAll("[data-ex]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.ex === id)));
    const board = $("#ex-board");
    board.classList.remove("in"); void board.offsetWidth;
    $("#ex-found").classList.remove("reveal");
    try {
      const d = await load(id);
      if (current !== id) return;
      paintIn(d); paintDid(d); paintFound(d, id, null); paintOut(d, id);
      $("#ex-foot").textContent = FOOT[id] || "";
      board.classList.add("in");
    } catch (e) { $("#ex-in").textContent = "Couldn't load the example: " + e.message; }
  }
  ROOT.addEventListener("click", (e) => {
    const t = e.target.closest("[data-ex]");
    if (t) { show(t.dataset.ex); return; }
    const go = e.target.closest("[data-ex-go]");
    if (go) { show(go.dataset.exGo); document.getElementById("h-ex").scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" }); return; }
    const g = e.target.closest("[data-guess-ex]");
    if (g && cache[current]) paintFound(cache[current], current, Number(g.dataset.guessEx));
  });

  // ------------------------------------------------------------------ the demo plant: what the tables hold for each part
  async function products() {
    try {
      const d = await R.api("/api/plant/products");
      let rows = 0;
      (d.products || []).forEach((p) => {
        rows += p.rows;
        const el = ROOT.querySelector(`.pl-card[data-part="${p.part_no}"] [data-lots]`);
        if (el) el.textContent = p.lots.toLocaleString("en");
      });
      $("#pl-src").textContent = `Five tables in BigQuery hold every lot, process log, inspection and shipment: ${rows.toLocaleString("en")} rows of lot data for the four parts. Read from ${d.source}.`;
    } catch (e) { /* the static numbers stay */ }
  }

  // ------------------------------------------------------------------ ask each Google Cloud service a small question, live
  const gcBtn = document.getElementById("gc-check");
  if (gcBtn) gcBtn.addEventListener("click", async () => {
    const sum = $("#gc-sum"), cards = [...ROOT.querySelectorAll(".gc-card")];
    gcBtn.disabled = true; sum.textContent = "Checking every service…";
    cards.forEach((c) => { c.dataset.s = "wait"; $(".gc-st", c).textContent = "Checking"; });
    const t0 = performance.now();
    try {
      const d = await R.api("/api/stack"), sv = d.services || {};
      let live = 0, ok = 0;
      cards.forEach((c, i) => {
        const v = sv[c.dataset.svc] || { ok: false, proof: "No answer." };
        const st = !v.ok ? "fail" : v.live ? "live" : "local";
        if (st === "live") live++;
        if (v.ok) ok++;
        setTimeout(() => {
          c.dataset.s = st;
          $(".gc-st", c).textContent = st === "live" ? "Live" : st === "local" ? "Local mode" : "Not answering";
          $(".gc-proof", c).innerHTML = `${esc(v.proof || "")}${v.ms ? ` <span>${(v.ms / 1000).toFixed(1)} s</span>` : ""}`;
        }, still ? 0 : 90 * i);
      });
      const when = new Date(d.checked_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
      const took = ((performance.now() - t0) / 1000).toFixed(1);
      sum.textContent = live ? `${live} of ${cards.length} answered live at ${when}, in ${took} s.`
        : `All ${ok} answered at ${when}, but this copy runs on a local machine, so none of them is the Google Cloud service.`;
      if (/\.(web\.app|firebaseapp\.com)$/.test(location.hostname)) {
        const fb = ROOT.querySelector('.gc-card[data-svc="firebase"] .gc-proof');
        if (fb) setTimeout(() => fb.insertAdjacentHTML("afterbegin", "<b>You are reading the Firebase Hosting copy right now.</b> "), still ? 0 : 800);
      }
    } catch (e) {
      sum.textContent = "The check failed: " + e.message;
      cards.forEach((c) => { c.dataset.s = ""; $(".gc-st", c).textContent = "Not checked"; });
    }
    gcBtn.disabled = false; gcBtn.textContent = "Check again";
  });

  let drawn = false;
  R.homeShow = () => { if (!drawn) { drawn = true; show("118"); products(); } };
  if (document.documentElement.dataset.view === "home") R.homeShow();
})();
