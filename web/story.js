/* Root & Escape: case 118 in six steps. Every number comes from the stored run or the plant tables, so the story and
   the workbench always agree. */
(function () {
  "use strict";
  const R = window.RAE;
  if (!R || !document.getElementById("story")) return;
  const $ = (s) => document.querySelector(s);
  const esc = R.esc;
  const pc = (x, d = 1) => (x == null ? "–" : (x * 100).toFixed(d) + "%");
  const pcs = (x) => (x == null ? "–" : (x * 100).toFixed(x < 0.01 ? 3 : 2) + "%");
  const num = (n) => Number(n).toLocaleString("en-US");
  const nb = (s) => String(s).replace(/(\d) (°C|%)/g, "$1\u00a0$2");
  const still = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = (iso) => { const d = new Date(String(iso).slice(0, 10) + "T00:00:00Z"); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
  const span = (a, b) => { const [x, y] = [day(a), day(b)]; return x.split(" ")[1] === y.split(" ")[1] ? `${x.split(" ")[0]} to ${y}` : `${x} to ${y}`; };
  const minus = (v) => (v < 0 ? "−" + Math.abs(v) : String(v));
  const CASE = "118";

  const VIS = $("#st-vis"), H = $("#st-h"), P = $("#st-p"), ACT = $("#st-act"), COUNT = $("#st-count"), DOTS = $("#st-dots"), ART = $("#st-art"), BY = $("#st-by");
  let f = null, at = 0, split = false, guess = null, holdAll = false, mailLang = "en", series = null, sheetLang = "en";

  // Which agent, tool and evidence produced what is on screen, so the Gen AI work is visible on every step.
  function credit(i) {
    const m = f.modes, model = esc(f.model || "Gemini"), code = (t) => `<code>${esc(t)}</code>`;
    const ev = (id) => (id ? ` <a class="evref" href="#118" data-ev-open="${esc(id)}">${esc(id)}</a>` : "");
    const inv = m.investigation === "gemini" ? `Investigator agent on ${model} through ADK` : "Investigator, scripted (Gemini off)";
    return ({
      1: m.intake === "gemini" ? `Intake agent on ${model}: translated the email, filled in the four facts and drew the box.` : "Intake: the stored reading (Gemini off).",
      2: `${inv} called ${code("stratify")} by crew within press${ev(f.crewId)}.` + (f.bqml ? ` BigQuery ML contribution analysis had already pointed at press ${esc(f.press)}.` : ""),
      3: `${inv} called ${code("param_excursions")}${ev(f.dewId)} and ${code("inspection_changes")}${ev(f.inspId)} for ${esc(f.press)}.`,
      4: `${inv} called ${code("containment")} over the shipments table${ev(f.holdId)}.`,
      5: `The Challenger is ${f.checks || "a set of"} rules in code. ` + (f.gemini ? `Claims that pass them also go to a critic on ${model}.` : "In the cloud, claims that pass also go to a Gemini critic."),
      6: m.writer === "gemini" ? `Writer agent on ${model}: every percentage and lot count it wrote was checked against the evidence.` : "Writer: template text (Gemini off).",
    })[i] || "";
  }

  // crews that actually ran a press: one crew may never work it (crew C never runs P4)
  const onPress = (st) => (st ? st.cells.filter((c) => c.units > 0).length : 0);
  // Step 2's caption: the gap inside the faulty press, in numbers, and why it doesn't count.
  function insideP4() {
    const o = f.cd.overall_contrast, st = f.cd.strata.find((s) => s.press === f.press), sh = shares().top;
    const hi = st && st.cells.find((c) => c.crew === o.high);
    const lo = st && st.cells.filter((c) => c.crew !== o.high).sort((a, b) => b.units - a.units)[0];
    const gap = hi && lo ? ` On ${f.press}, crew ${lo.crew} is at ${pc(lo.rate)} and crew ${o.high} at ${pc(hi.rate)}, a gap the test can't tell from chance.` : "";
    const n = onPress(st);
    const who = n === f.cd.overall.length ? `Every crew cracks clips on ${f.press}` :
      n === 2 ? `Both crews that run ${f.press} crack clips there` : `Every crew that runs ${f.press} cracks clips there`;
    return `${who}, and hardly any elsewhere.${gap} Crew ${o.high} just runs ${f.press} the most: ${sh}% of its output.`;
  }

  // ------------------------------------------------------------------------------------- facts from the run
  function facts(d) {
    const c = d.case, r = d.result || {};
    const evOf = (tool, ok = () => true) => (r.evidence || []).find((e) => e.tool === tool && ok(e.args || {}));
    const it = r.intake || {}, a = r.analysis || {}, dr = r.draft || {};
    const crew = evOf("stratify", (x) => x.by === "crew"), dew = evOf("param_excursions"), insp = evOf("inspection_changes");
    const how = String((it.d2 && it.d2.how_many) || "").match(/(\d[\d,]*)\D+?(\d[\d,]*)/);
    const hours = String(it.urgency || "").match(/(\d+)\s*(?:hours?|h\b|時間)/i);
    const last = (r.challenger || []).slice(-1)[0] || { rules: [] };
    const cd = crew && crew.data && crew.data.confounded ? crew.data : null;
    return {
      c, r, it, bad: how ? how[1] : null, hours: hours ? hours[1] : null,
      press: (a.primary_segment || {}).press, rows: (a.heatmap || {}).rows || [],
      cd, accused: cd ? cd.overall_contrast.high : null,
      dew: dew && ((dew.data || {}).params || []).find((x) => x.flag), dewId: dew && dew.id,
      insp: insp && ((insp.data || {}).sampling_changes || [])[0], inspId: insp && insp.id,
      hold: r.containment, occ: (dr.occurrence || {}).statement || "", escTxt: (dr.escape || {}).statement || "",
      report: (r.report || {}).en || {}, reportJa: (r.report || {}).ja || null, checks: last.rules.length,
      modes: r.modes || {}, bqml: !!(a.bqml_insights || []).length, holdId: (evOf("containment") || {}).id,
      occEv: (dr.occurrence || {}).evidence || [], escEv: (dr.escape || {}).evidence || [],
      secs: r.duration_s, gemini: (r.modes || {}).investigation === "gemini", model: r.model,
    };
  }

  // ------------------------------------------------------------------------------------------- the steps
  const STEPS = [
    () => ({
      count: "For supplier quality engineers answering a customer complaint",
      h: f.accused && f.press ? `The data blamed crew ${f.accused}. The cause was press ${f.press}.` : "Find the real cause, not the easy one.",
      p: `A car maker found ${esc(f.bad || "")} cracked clips and gave the plant ${f.hours ? esc(f.hours) + " hours" : "a day"} to start its ${R.term("8d", "8D report")}. ` +
        "Root & Escape's Gemini agents tested every suspect against the plant's records in BigQuery, held only the suspect lots and drafted the report for a quality engineer to sign.",
      html: true,
      act: `<button class="btn big" type="button" data-st="1">See how, in six steps</button>
            <button class="btn ghost big" type="button" data-bench="${CASE}">Open the workbench</button>
            <p class="st-more">Judging it? <a class="link inline" href="#test" data-nav="lab">Test it yourself</a> with 17 ready-made checks.</p>`,
      draw: (el) => { el.innerHTML = hero() + coverFacts(); },
    }),
    () => ({
      count: "Step 1 of 6: the complaint",
      h: "It arrives in Japanese, with a photo.",
      p: "Gemini translates the email, pulls out the facts and boxes the crack next to a known-good part.",
      act: `<div class="seg" role="group" aria-label="Email language">
              <button type="button" class="${mailLang === "en" ? "on" : ""}" data-mail="en" aria-pressed="${mailLang === "en"}">In English</button>
              <button type="button" class="${mailLang === "ja" ? "on" : ""}" data-mail="ja" aria-pressed="${mailLang === "ja"}">Original (日本語)</button></div>`,
      draw: (el) => { el.innerHTML = `<div class="st-two">${photo({ golden: true, draw: true })}${mail()}</div>`; },
    }),
    () => {
      if (!f.cd) return { count: "Step 2 of 6: the suspects", h: "Every suspect is tested.", p: "Press, cavity, shift, crew and resin lot are each split against the others.", act: "", draw: (el) => { el.innerHTML = ""; } };
      const o = f.cd.overall_contrast;
      if (!split) return {
        count: "Step 2 of 6: the suspect",
        h: `Crew ${o.high} cracks ${o.ratio}× more clips than crew ${o.low}.`,
        p: `That's what the spreadsheet shows. Would you blame crew ${o.high}?`,
        act: `<button class="btn big" type="button" data-guess="yes">Yes, retrain crew ${esc(o.high)}</button>
              <button class="btn ghost big" type="button" data-guess="no">Check each press first</button>`,
        draw: (el) => { el.innerHTML = chart(); },
      };
      return {
        count: "Step 2 of 6: the suspect",
        h: guess === "yes" ? "That's the trap. Split by press, the gap disappears." : guess === "no" ? "Good call. Split by press, the gap disappears." : "Split by press, the gap disappears.",
        p: insideP4(),
        act: `<button class="btn ghost" type="button" data-split="0">Show it by crew again</button>`,
        draw: (el) => { el.innerHTML = chart(); },
      };
    },
    () => ({
      count: "Step 3 of 6: the cause",
      h: f.dew ? `Press ${f.press}'s resin dryer was running wet.` : `The cause is on press ${f.press}.`,
      p: "Wet resin moulds brittle clips. " + (f.insp ? `Inspection on ${f.press} had just been cut, so the cracked ones shipped.` : ""),
      art: `<img src="/static/photos/press.jpg" alt="An injection moulding press with a robot arm and a red, amber and green stack light" width="830" height="470">
        <figcaption>A press is an injection moulding machine like this one: resin pellets are dried, melted and shot into a mould.
        Photo: <a href="https://commons.wikimedia.org/wiki/File:Injection_Molding_Machine_with_a_robot_arm.jpg" target="_blank" rel="noopener">The robotic engineer1</a>,
        <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener">CC BY-SA 4.0</a>, cropped.</figcaption>`,
      act: "",
      draw: (el) => {
        el.innerHTML = `<div class="st-stack"><figure class="cc" id="st-cc">${f.dew ? `<p class="chart-t">Loading ${esc(f.press)}'s dryer log…</p>` : ""}</figure>${f.insp ? checks() : ""}</div>`;
        if (f.dew) drawControl();
      },
    }),
    () => {
      const c = f.hold;
      return {
        count: "Step 4 of 6: containment",
        h: `Hold ${c.targeted_lots} lots, not ${c.blanket_lots}.`,
        p: `Only ${c.segment_label}, made ${span(c.suspect_window[0], c.suspect_window[1])}. ` +
          `That's ${Math.round(100 - (100 * c.targeted_parts) / c.blanket_parts)}% fewer parts stopped, and the customer's line keeps running.`,
        act: `<div class="seg" role="group" aria-label="Which hold">
                <button type="button" class="${holdAll ? "" : "on"}" data-hold="0" aria-pressed="${!holdAll}">Targeted: ${c.targeted_lots} lots</button>
                <button type="button" class="${holdAll ? "on" : ""}" data-hold="1" aria-pressed="${holdAll}">Hold everything: ${c.blanket_lots}</button></div>
              <span class="st-num">${num(holdAll ? c.blanket_parts : c.targeted_parts)} parts held</span>`,
        draw: (el) => { el.innerHTML = grid(); },
      };
    },
    () => ({
      count: "Step 5 of 6: the Challenger",
      h: f.accused ? `Now try to blame crew ${f.accused} anyway.` : "Now try to break the answer.",
      p: `Every draft has to pass ${f.checks || "a set of"} checks written in code before a person sees it. Send a claim and see.`,
      act: `<button class="btn big" type="button" data-claim="blame">Send "operator error"</button>
            <button class="btn ghost big" type="button" data-claim="real">Send the agents' answer</button>`,
      draw: (el) => { el.innerHTML = claimCard(); },
    }),
    () => ({
      count: "Step 6 of 6: the report",
      h: f.secs && f.gemini ? `The 8D report, drafted in ${Math.round(f.secs)} seconds.` : "The 8D report, drafted and checked.",
      p: "In English and Japanese, with every claim linked to its evidence. A quality engineer signs before it goes out.",
      act: `<button class="btn big" type="button" data-nav="lab">Test it yourself</button>
            <button class="btn ghost big" type="button" data-bench="${CASE}">Open the workbench</button>
            <span class="st-more">Two more cases: <button class="link" type="button" data-bench="124">burrs with two causes</button> and <button class="link" type="button" data-bench="121">scratches with no cause in the data</button>. <button class="link" type="button" data-how>How the agents work</button></span>`,
      draw: (el) => { el.innerHTML = sheet(); },
    }),
  ];

  // ------------------------------------------------------------------------------------------ visuals
  function photo({ tag = false, golden = false, draw = false } = {}) {
    const c = f.c, b = f.it.box_2d, h = f.hold;
    const box = b && b.length === 4 ? `<span class="st-box${draw ? " draw" : ""}" style="top:${b[0] / 10}%;left:${b[1] / 10}%;height:${(b[2] - b[0]) / 10}%;width:${(b[3] - b[1]) / 10}%"></span>` : "";
    const gold = golden && c.golden ? `<span class="st-gold"><img src="/static/photos/${esc(c.golden)}" alt="Known-good clip for comparison"><span>Good part</span></span>` : "";
    return `<figure class="st-photo">
      <div class="st-ph"><img src="/static/photos/${esc(c.photo)}" alt="Customer photo: a snap clip cracked at the root of its hook">${box}${gold}</div>
      ${tag && h ? `<span class="hold-tag" aria-hidden="true"><b>Hold</b><span>${esc(h.segment_label)}</span><span>${esc(span(h.suspect_window[0], h.suspect_window[1]))}</span></span>` : ""}
      <figcaption>${draw && f.it.photo_observation ? esc(f.it.photo_observation) : "Customer photo for case 118. Synthetic, like all the plant data here."}</figcaption>
    </figure>`;
  }

  // The cover: a real car body line for the stakes, with the customer's (synthetic) defect photo pinned on it.
  function hero() {
    const c = f.c, b = f.it.box_2d, h = f.hold;
    const box = b && b.length === 4 ? `<span class="st-box" style="top:${b[0] / 10}%;left:${b[1] / 10}%;height:${(b[2] - b[0]) / 10}%;width:${(b[3] - b[1]) / 10}%"></span>` : "";
    return `<figure class="st-hero">
      <img class="st-hero-img" src="/static/photos/hero_body_line.jpg" alt="Robot arms working on a car body on an assembly line" width="1600" height="924">
      <div class="st-inset">
        <div class="st-ph"><img src="/static/photos/${esc(c.photo)}" alt="The customer's photo: a snap clip cracked at the root of its hook">${box}</div>
        <span class="st-inset-l">The customer's photo</span>
        ${h ? `<span class="hold-tag" aria-hidden="true"><b>Hold</b><span>${esc(h.segment_label)}</span><span>${esc(span(h.suspect_window[0], h.suspect_window[1]))}</span></span>` : ""}
      </div>
      <figcaption>Car body line, photo by <a href="https://unsplash.com/photos/robotic-arms-assembling-a-car-chassis-on-a-factory-line-EyqUxJuOb1Q" target="_blank" rel="noopener">Lilian Do Khac</a> on Unsplash. The clip photo is synthetic, like all the plant data here.</figcaption>
    </figure>`;
  }

  function coverFacts() {
    const h = f.hold, items = [];
    if (h) items.push([`${h.targeted_lots}<small> of ${h.blanket_lots}</small>`, "lots held"]);
    if (f.cd) items.push(["0", "people blamed"]);
    if (f.secs && f.gemini) items.push([`${Math.round(f.secs)}<small> s</small>`, `from email to 8D, on ${esc(f.model || "Gemini")}`]);
    else if (f.checks) items.push([`${f.checks}<small> of ${f.checks}</small>`, "checks passed"]);
    return `<dl class="st-facts">${items.map(([v, k]) => `<div><dd>${v}</dd><dt>${k}</dt></div>`).join("")}</dl>`;
  }

  function mail() {
    const c = f.c, it = f.it, d2 = it.d2 || {};
    const ja = mailLang === "ja" || !it.translation_en;
    const short = String(ja ? c.complaint : it.translation_en).replace(/\s+/g, " ").trim();
    const what = String(d2.what || c.defect).split(" (")[0];
    const rows = [["Defect", what], ["How many", d2.how_many], ["Date codes", span(c.date_code_start, c.date_code_end)], ["Deadline", it.urgency]].filter((x) => x[1]);
    return `<div class="mail">
      <div class="mail-h"><b>${esc(c.customer)}</b><span>${esc(day(c.received))}, ${esc(String(c.received).slice(11, 16))}</span></div>
      <p class="mail-b"${ja ? ' lang="ja"' : ""}>${esc(short.length > 250 ? short.slice(0, 248) + "…" : short)}</p>
      <dl class="mail-f">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      <p class="mail-by">${f.r.modes && f.r.modes.intake === "gemini" ? `Read by ${esc(f.model || "Gemini")}` : "Stored reading, Gemini off"}</p>
    </div>`;
  }

  function shares() {
    const st = f.cd.strata.find((s) => s.press === f.press);
    if (!st) return { top: 0, list: [] };
    const tot = st.cells.reduce((s, c) => s + c.units, 0) || 1;
    const list = f.cd.overall.map((o) => { const c = st.cells.find((x) => x.crew === o.crew); return { crew: o.crew, share: c ? c.units / tot : 0 }; });
    const hi = list.find((x) => x.crew === f.cd.overall_contrast.high);
    return { top: Math.round(100 * (hi ? hi.share : 0)), list };
  }
  function otherRange() {
    const rates = f.cd.strata.filter((s) => s.press !== f.press).flatMap((s) => s.cells.map((c) => c.rate));
    return rates.length ? [Math.min(...rates), Math.max(...rates)] : [0, 0];
  }

  // One bar per crew splits into one piece per press. Each piece keeps its share of the crew's output as its width,
  // then moves to its press and takes its own crack rate as its height.
  function chart() {
    const cd = f.cd, crews = cd.overall.map((o) => o.crew), strata = cd.strata, G = 100 / strata.length;
    const hi = Math.max(...strata.flatMap((s) => s.cells.map((c) => c.rate)), ...cd.overall.map((o) => o.rate));
    const top = Math.max(0.01, Math.ceil(hi * 100) / 100), y = (r) => (100 * r) / top;
    const accused = cd.overall_contrast.high;
    let pieces = "", labels = "";
    crews.forEach((cr, i) => {
      const o = cd.overall.find((x) => x.crew === cr), mid = (i + 0.5) * (100 / crews.length), W = 17;
      let acc = mid - W / 2;
      strata.forEach((s, g) => {
        const c = s.cells.find((x) => x.crew === cr);
        if (!c) return;
        const w = (W * c.units) / o.units, sx = g * G + G * (0.13 + i * 0.255), sw = G * 0.22;
        pieces += `<i class="pc k${i}" style="--px:${acc}%;--pw:${w}%;--ph:${y(o.rate)}%;--sx:${sx}%;--sw:${sw}%;--sh:${y(c.rate)}%" title="Crew ${esc(cr)} on ${esc(s.press)}: ${pc(c.rate, 2)} of ${num(c.units)} parts"></i>`;
        if (s.press === f.press) {
          // a label over a bar with a taller right-hand neighbour would sit on that neighbour, so it goes to the bar's left
          const taller = s.cells.some((x) => crews.indexOf(x.crew) > i && x.rate > c.rate);
          labels += `<span class="val on-split${taller ? " left" : ""}" style="left:${taller ? sx : sx + sw / 2}%;bottom:calc(${y(c.rate)}% + 5px)">${esc(cr)} ${pc(c.rate)}</span>`;
        }
        acc += w;
      });
      labels += `<span class="val on-pool" style="left:${mid}%;bottom:calc(${y(o.rate)}% + 6px)">${pcs(o.rate)}</span>` +
        `<span class="ax on-pool" style="left:${mid}%">Crew ${esc(cr)}</span>`;
      if (cr === accused) labels += `<span class="alarm on-pool" style="left:calc(${mid}% + ${W / 2}% + 8px);bottom:calc(${y(o.rate)}% - 13px)"><b>${cd.overall_contrast.ratio}×</b> crew ${esc(cd.overall_contrast.low)}'s rate</span>`;
    });
    strata.forEach((s, g) => { labels += `<span class="ax on-split${s.press === f.press ? " hot" : ""}" style="left:${(g + 0.5) * G}%">${esc(s.press)}</span>`; });
    const gi = strata.findIndex((s) => s.press === f.press);
    if (gi >= 0) {
      const peak = Math.max(...strata[gi].cells.map((c) => c.rate));
      const n = onPress(strata[gi]), whom = n === cd.overall.length ? "every crew" : n === 2 ? "both crews" : "every crew on it";
      labels += `<span class="alarm on-split" style="left:calc(${(gi + 1) * G}% + 6px);bottom:calc(${y(peak)}% - 13px)"><b>${esc(f.press)}</b> high for ${whom}</span>`;
    }
    let lines = "";
    for (let t = 1; t <= Math.round(top * 100); t++) lines += `<span class="gl" style="bottom:${y(t / 100)}%"><em>${t}%</em></span>`;
    const sh = shares(), [lo, hi2] = otherRange();
    const aria = `Crack rate by crew: ${cd.overall.map((o) => `${o.crew} ${pc(o.rate, 2)}`).join(", ")}. Split by press, on ${f.press}: ` +
      (gi >= 0 ? strata[gi].cells.map((c) => `crew ${c.crew} ${pc(c.rate, 2)}`).join(", ") : "") + `; on the other presses every crew is between ${pc(lo)} and ${pc(hi2)}.`;
    return `<div class="chart${split ? " is-split" : ""}">
      <p class="chart-t">Crack rate for date codes ${esc(span(cd.window[0], cd.window[1]))}, <span class="on-pool">by crew</span><span class="on-split">by crew inside each press</span></p>
      <div class="plot" role="img" aria-label="${esc(aria)}">${lines}${gi >= 0 ? `<span class="band on-split" style="left:${gi * G}%;width:${G}%"></span>` : ""}${pieces}${labels}</div>
      <div class="legend">${crews.map((cr, i) => `<span><i class="k${i}"></i>Crew ${esc(cr)}</span>`).join("")}
        <span class="on-split share">Who ran press ${esc(f.press)}: ${sh.list.map((x) => `crew ${esc(x.crew)} ${Math.round(100 * x.share)}%`).join(", ")}</span></div>
    </div>`;
  }

  // Shewhart chart of the dryer log: centre line and 3-sigma limits from the 28 days before the complaint,
  // the resin maker's limit, and the press's daily crack rate on the same time axis.
  async function drawControl() {
    const el = $("#st-cc");
    try {
      if (!series) series = await R.api(`/api/plant/series?press=${encodeURIComponent(f.press)}&param=${encodeURIComponent(f.dew.param)}&defect=${encodeURIComponent(f.c.defect)}`);
    } catch (err) { if (el) el.innerHTML = `<p class="chart-t">The dryer log didn't load. ${esc(f.dew.label)} averaged ${minus(f.dew.test_mean)} against a limit of ${minus(f.dew.spec_max)}.</p>`; return; }
    if (!el || !el.isConnected) return;
    const d = f.dew, pts = series.points.map(([t, v]) => [Date.parse(t + ":00Z"), v]);
    const cl = d.control_mean, ucl = cl + 3 * d.control_sd, lcl = cl - 3 * d.control_sd, spec = series.spec_max;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] + 864e5 / 3;
    const vals = pts.map((p) => p[1]).concat([ucl, lcl, spec ?? ucl]);
    const v0 = Math.floor(Math.min(...vals) / 5) * 5, v1 = Math.ceil(Math.max(...vals) / 5) * 5;
    const narrow = el.clientWidth < 520, W = narrow ? 420 : 640, L = 34, Rm = narrow ? 76 : 92, T = 24, Hc = narrow ? 150 : 170, gap = 34, Hb = 54;
    const X = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - Rm), Y = (v) => T + ((v1 - v) / (v1 - v0)) * Hc;
    const ws = Date.parse(f.c.date_code_start + "T00:00:00Z"), we = Date.parse(f.c.date_code_end + "T00:00:00Z") + 864e5;
    let s = `<rect class="win" x="${X(ws)}" y="${T - 4}" width="${X(we) - X(ws)}" height="${Hc + gap + Hb + 4}"/>`;
    for (let v = Math.ceil(v0 / 10) * 10; v <= v1; v += 10) s += `<line class="grid" x1="${L}" x2="${W - Rm}" y1="${Y(v)}" y2="${Y(v)}"/><text class="yl" x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${minus(v)}</text>`;
    const ref = (v, cls, name) => `<line class="${cls}" x1="${L}" x2="${W - Rm}" y1="${Y(v)}" y2="${Y(v)}"/><text class="${cls}" x="${W - Rm + 6}" y="${Y(v) + 4}">${name}</text>`;
    s += ref(ucl, "ucl", `UCL ${minus(ucl.toFixed(1))}`) + ref(cl, "cl", `CL ${minus(cl.toFixed(1))}`) + ref(lcl, "ucl", `LCL ${minus(lcl.toFixed(1))}`);
    if (spec != null) s += ref(spec, "spec", `Limit ${minus(spec)}`);
    s += `<polyline class="trace" pathLength="1" points="${pts.map(([t, v]) => `${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}"/>`;
    s += pts.map(([t, v]) => `<circle class="${v > ucl || v < lcl ? "out" : "in"}" cx="${X(t).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="${v > ucl || v < lcl ? 3 : 1.8}"/>`).join("");
    const by = T + Hc + gap, dr = series.daily_rate.filter(([, r]) => r != null), rmax = Math.max(0.01, ...dr.map(([, r]) => r));
    const base = f.r.analysis && f.r.analysis.overall ? f.r.analysis.overall.rate_control : 0;
    s += `<line class="sep" x1="${L}" x2="${W - Rm}" y1="${by - gap / 2 - 2}" y2="${by - gap / 2 - 2}"/><text class="lab" x="${L}" y="${by - 4}">${esc(f.press)} crack rate per day, peak ${pc(rmax)}</text>`;
    s += dr.map(([dt, r]) => { const x = X(Date.parse(dt + "T00:00:00Z")), h = (r / rmax) * Hb; return `<rect class="${base && r > 3 * base ? "hotbar" : "bar"}" x="${x.toFixed(1)}" y="${(by + Hb - h).toFixed(1)}" width="${Math.max(2, X(t0 + 864e5) - X(t0) - 1.5).toFixed(1)}" height="${h.toFixed(1)}"/>`; }).join("");
    s += `<line class="axis" x1="${L}" x2="${W - Rm}" y1="${by + Hb}" y2="${by + Hb}"/>`;
    for (let t = Date.parse(new Date(t0).toISOString().slice(0, 10) + "T00:00:00Z") + 864e5; t < t1; t += (narrow ? 14 : 7) * 864e5) s += `<text class="xl" x="${X(t)}" y="${by + Hb + 16}" text-anchor="middle">${esc(day(new Date(t).toISOString()))}</text>`;
    s += `<text class="win" x="${(X(ws) + X(we)) / 2}" y="${T - 8}" text-anchor="middle">date codes</text>`;
    el.innerHTML = `<p class="chart-t">${esc(f.press)} resin dryer dew point, °C, one reading a shift <a class="evref" href="#118" data-ev-open="${esc(f.dewId)}" title="Open this evidence in the workbench">${esc(f.dewId)}</a></p>
      <svg viewBox="0 0 ${W} ${by + Hb + 22}" role="img" aria-label="Control chart: ${esc(d.label)} on ${esc(f.press)} stays near ${minus(cl.toFixed(1))} until early October, then rises above the ${minus(spec)} limit to an average of ${minus(d.test_mean)} during the date codes, while the press's daily crack rate climbs.">${s}</svg>
      <p class="cc-k"><span><i class="out"></i>Reading outside the control limits</span><span>Lower is drier</span></p>`;
  }

  function checks() {
    const s = f.insp, dot = (n) => Array.from({ length: n }, () => "<i></i>").join("");
    return `<figure class="checks">
      <p class="chart-t">Why it got out: inspection checks per hour on ${esc(f.press)} <a class="evref" href="#118" data-ev-open="${esc(f.inspId)}" title="Open this evidence in the workbench">${esc(f.inspId)}</a></p>
      <div class="ck"><span>Until ${esc(day(s.date))}</span><b>${dot(s.sampling_from)}</b><em>${s.sampling_from} an hour</em></div>
      <div class="ck cut"><span>From ${esc(day(s.date))}</span><b>${dot(s.sampling_to)}</b><em>${s.sampling_to} an hour</em></div>
    </figure>`;
  }

  // Lots as a press-by-day grid: three shifts a day, one tile per lot.
  function grid() {
    const c = f.hold, rows = f.rows, days = c.blanket_lots / Math.max(1, rows.length * 3);
    const held = new Map(c.lots.map((l) => [`${l.press}|${l.prod_date}|${l.shift}`, l]));
    const st = c.by_status || {};
    let cells = "", head = "", n = 0;
    if (rows.length && Number.isInteger(days)) {
      const t0 = Date.parse(c.suspect_window[0] + "T00:00:00Z");
      for (let i = 0; i < days; i++) head += `<span style="grid-column:${2 + i * 3} / span 3">${i === 0 ? esc(day(c.suspect_window[0])) : new Date(t0 + i * 864e5).getUTCDate()}</span>`;
      rows.forEach((pr) => {
        cells += `<span class="rl${pr === f.press ? " hot" : ""}">${esc(pr)}</span>`;
        for (let i = 0; i < days; i++) {
          const dt = new Date(t0 + i * 864e5).toISOString().slice(0, 10);
          for (const s of ["D", "S", "N"]) {
            const l = held.get(`${pr}|${dt}|${s}`);
            cells += `<i class="${l ? "held" : ""}" style="--d:${l ? n++ : 0}" title="${l ? `${esc(l.lot_id)}, ${esc(l.status.replace("_", " "))}` : `${esc(pr)}, ${esc(day(dt))}, shift ${s}`}"></i>`;
          }
        }
      });
      return `<div class="lots-grid${holdAll ? " all" : ""}" style="--cols:${days * 3}">
        <p class="chart-t">Every lot made since ${esc(day(c.suspect_window[0]))}, one tile per press, day and shift</p>
        <div class="lg" role="img" aria-label="${c.blanket_lots} lots; ${c.targeted_lots} held, all from ${esc(c.segment_label)}"><span></span>${head}${cells}</div>
        <p class="lg-k"><span><i class="held"></i>On hold</span><span><i></i>Released</span><span class="if-t">Of the held lots, ${st.at_customer || 0} are already at the customer and ${st.in_transit || 0} in transit.</span><span class="if-a">A blanket hold stops all ${c.blanket_lots} lots, ${num(c.blanket_parts)} parts, including every press that was fine.</span></p>
      </div>`;
    }
    for (let i = 0; i < c.blanket_lots; i++) cells += `<i class="${i < c.targeted_lots ? "held" : ""}" style="--d:${i}"></i>`;
    return `<div class="lots-grid simple${holdAll ? " all" : ""}"><div class="lg">${cells}</div></div>`;
  }

  function claimCard() {
    return `<div class="claim">
      <p class="chart-t">The claim</p>
      <blockquote id="st-claim">${esc(claims().blame.occ)}</blockquote>
      <div class="claim-out" id="st-out" aria-live="polite"><span class="hint">Pick a claim to send it to the Challenger.</span></div>
    </div>`;
  }
  const claims = () => ({
    blame: { occ: `Operator error: crew ${f.accused || "B"} did not follow the work instruction. Retrain crew ${f.accused || "B"}.`, esc: f.escTxt },
    real: { occ: f.occ, esc: f.escTxt },
  });
  // The is / is not view behind the people rule: crew by crew, inside the faulty press and outside it.
  function insideTable() {
    if (!f.cd) return "";
    const crews = f.cd.overall.map((o) => o.crew), p4 = f.cd.strata.find((s) => s.press === f.press);
    const rest = (cr) => { const r = f.cd.strata.filter((s) => s.press !== f.press).map((s) => s.cells.find((c) => c.crew === cr)).filter(Boolean).map((c) => c.rate); return r.length ? `${pc(Math.min(...r))} to ${pc(Math.max(...r))}` : "–"; };
    const on = (cr) => { const c = p4 && p4.cells.find((x) => x.crew === cr); return c ? pc(c.rate) : "never ran it"; };
    return `<table class="inside"><thead><tr><th>Crack rate</th>${crews.map((c) => `<th class="${c === f.accused ? "acc" : ""}">Crew ${esc(c)}</th>`).join("")}</tr></thead>
      <tbody><tr><th>On ${esc(f.press)}</th>${crews.map((c) => `<td>${on(c)}</td>`).join("")}</tr><tr><th>Other presses</th>${crews.map((c) => `<td>${rest(c)}</td>`).join("")}</tr></tbody></table>`;
  }

  function sheet() {
    // first sentence of each section, without a leading "Team:" style label that would repeat the row name
    const ja = sheetLang === "ja" && f.reportJa, rep = ja ? f.reportJa : f.report, cut = (s) => {
      s = String(s || "").replace(/\s+/g, " ").replace(ja ? /^[^：:、。]{1,8}[：:]\s*/ : /^[A-Z][A-Za-z ]{2,28}:\s+(?=\S)/, "");
      s = s.charAt(0).toUpperCase() + s.slice(1);
      const m = s.match(ja ? /^.{12,90}?。/ : /^.{20,170}?[.;](\s|$)/);
      if (m) return nb(m[0].trim().replace(/;$/, "."));
      return nb(s.length > 170 ? s.slice(0, s.lastIndexOf(" ", 168)) + "…" : s);
    };
    const names = ja ? { D1: "チーム", D2: "問題", D3: "暫定処置", D4: "発生原因", D4_escape: "流出原因", D5: "是正処置", D6: "効果確認", D7: "再発防止", D8: "称賛" }
      : { D1: "Team", D2: "Problem", D3: "Containment", D4: "Why it happened", D4_escape: "Why it got out", D5: "Actions", D6: "Verify", D7: "Prevent", D8: "Close" };
    const chips = { D3: [f.holdId], D4: f.occEv, D4_escape: f.escEv };
    const part = (String(f.c.title).match(/\b[A-Z]{1,4}-\d{2,5}\b/) || [""])[0];
    return `<div class="sheet"${ja ? ' lang="ja"' : ""}>
      ${f.reportJa ? `<div class="seg small" role="group" aria-label="Report language"><button type="button" class="${ja ? "" : "on"}" data-sheet="en" aria-pressed="${!ja}">English</button><button type="button" class="${ja ? "on" : ""}" data-sheet="ja" aria-pressed="${!!ja}">日本語</button></div>` : ""}
      <ol>${Object.keys(names).filter((k) => rep[k]).map((k) => `<li><b>${k === "D4_escape" ? "" : k}</b><span><em>${names[k]}${ja ? "：" : "."}</em>${ja ? "" : " "}${esc(cut(rep[k]))}` +
        `${(chips[k] || []).filter(Boolean).map((id) => ` <a class="evref" href="#118" data-ev-open="${esc(id)}">${esc(id)}</a>`).join("")}</span></li>`).join("")}</ol>
      <table class="title-block"><tr><th>Document</th><td>8D-${CASE}</td><th>Part</th><td>${esc(part || "–")}</td><th>Customer</th><td>${esc(f.c.customer)}</td></tr>
        <tr><th>Issued</th><td>${esc(day(f.r.created_at))}</td><th>Status</th><td>Draft</td><th>Approved by</th><td class="sig"></td></tr></table>
    </div>`;
  }

  // ------------------------------------------------------------------------------------------- engine
  function show(i, { focus = false } = {}) {
    at = Math.max(0, Math.min(STEPS.length - 1, i));
    const s = STEPS[at]();
    COUNT.textContent = s.count;
    H.textContent = s.h;
    if (s.html) P.innerHTML = s.p; else P.textContent = s.p;
    BY.innerHTML = credit(at);
    BY.hidden = !BY.innerHTML;
    ACT.innerHTML = s.act;
    ART.innerHTML = s.art || "";
    ART.hidden = !s.art;
    document.getElementById("story").dataset.step = String(at);
    const swap = () => { s.draw(VIS); VIS.classList.remove("out"); };
    if (still() || !VIS.childElementCount) swap(); else { VIS.classList.add("out"); setTimeout(swap, 140); }
    DOTS.innerHTML = STEPS.map((_, k) => `<li><button type="button" data-st="${k}" class="${k === at ? "on" : k < at ? "seen" : ""}" aria-label="${k === 0 ? "Start" : "Step " + k}"${k === at ? ' aria-current="step"' : ""}></button></li>`).join("");
    $("#st-back").disabled = at === 0;
    $("#st-next").textContent = at === STEPS.length - 1 ? "Workbench" : "Next";
    $("#st-next").hidden = at === 2 && !!f.cd && !split;  // the two answers are the way on
    if (focus) H.focus({ preventScroll: true });
  }

  function next() {
    if (at === 2 && f.cd && !split) return setSplit(true);  // the reveal is the point of this step, so show it before moving on
    if (at === STEPS.length - 1) return R.openCase(CASE);
    show(at + 1, { focus: true });
  }

  function setSplit(v) {
    split = v;
    if (!v) guess = null;
    const s = STEPS[2]();
    H.textContent = s.h; P.textContent = s.p; ACT.innerHTML = s.act;
    $("#st-next").hidden = !!f.cd && !v;
    const ch = VIS.querySelector(".chart");
    if (ch) ch.classList.toggle("is-split", v); else s.draw(VIS);
  }

  function setHold(v) {
    holdAll = v;
    const g = VIS.querySelector(".lots-grid");
    if (g) g.classList.toggle("all", v);
    ACT.innerHTML = STEPS[4]().act;
  }

  async function sendClaim(kind) {
    const c = claims()[kind], out = $("#st-out");
    if (!out) return;
    $("#st-claim").textContent = c.occ;
    document.querySelectorAll("[data-claim]").forEach((b) => b.classList.toggle("on", b.dataset.claim === kind));
    out.innerHTML = `<span class="hint">Checking…</span>`;
    try {
      const r = await R.api(`/api/cases/${CASE}/challenge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ occurrence: c.occ, escape: c.esc }) });
      const bad = r.rules.filter((x) => !x.passed);
      const people = bad.find((x) => x.rule === "no_people_blame_without_stratified_effect");
      const first = people || bad[0];
      out.innerHTML = r.verdict === "REJECTED"
        ? `<span class="stamp big">Rejected</span><p class="why"><b>${esc(R.ruleName(first.rule))}.</b> ${esc(first.message)}</p>` + (people ? insideTable() : "") +
          (bad.length > 1 ? `<p class="hint">Also failed: ${bad.filter((x) => x !== first).map((x) => `"${esc(R.ruleName(x.rule))}"`).join(", ")}.</p>` : "")
        : `<span class="stamp ok big">Passed</span><p class="why">All ${r.rules.length} checks pass. It still waits for a person's signature.</p>`;
    } catch (err) { out.innerHTML = `<span class="hint">${esc(err.message)}</span>`; }
  }

  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-st],[data-split],[data-guess],[data-hold],[data-mail],[data-claim],[data-ev-open],[data-sheet],[data-how]");
    if (!t || !t.closest("#story")) return;
    if (t.dataset.st != null) show(Number(t.dataset.st), { focus: true });
    else if (t.dataset.guess) { guess = t.dataset.guess; setSplit(true); }
    else if (t.dataset.split != null) setSplit(t.dataset.split === "1");
    else if (t.dataset.hold != null) setHold(t.dataset.hold === "1");
    else if (t.dataset.mail) { mailLang = t.dataset.mail; show(at); }
    else if (t.dataset.claim) sendClaim(t.dataset.claim);
    else if (t.dataset.sheet) { sheetLang = t.dataset.sheet; VIS.innerHTML = sheet(); }
    else if (t.dataset.how != null) $("#how-btn").click();
    else if (t.dataset.evOpen) { e.preventDefault(); R.openCase(CASE, { ev: t.dataset.evOpen }); }
  });
  $("#st-next").addEventListener("click", next);
  $("#st-back").addEventListener("click", () => (at === 2 && split ? setSplit(false) : show(at - 1, { focus: true })));
  document.addEventListener("keydown", (e) => {
    if (document.documentElement.dataset.view !== "story" || !f || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.target.closest("input,textarea,select,[contenteditable]") || document.querySelector("dialog[open]")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); next(); }
    else if (e.key === "ArrowLeft" && at > 0) { e.preventDefault(); $("#st-back").click(); }
  });

  async function load() {
    try {
      const d = await R.api(`/api/cases/${CASE}`);
      if (!d.result) throw new Error("not run yet");
      f = facts(d);
      show(0);
    } catch (err) {
      H.textContent = "Find the real cause, not the easy one.";
      P.textContent = "The example run isn't ready yet. Open the workbench and press Run live to start it.";
      ACT.innerHTML = `<button class="btn big" type="button" data-bench="${CASE}">Open the workbench</button>`;
    }
  }
  R.onResult = (id, d) => { if (id === CASE && d && d.result) f = facts(d); };
  R.storyGo = (i) => { if (f) show(i == null ? at : i, { focus: i != null }); };
  load();
})();
