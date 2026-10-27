/* Agent runs on the pages: start, poll, and a tabbed run view (Result / How the agents decided / Evidence).
   Shared by My signal (signal.js) and Council view (council.js). Every number shown comes from the run's
   result or its evidence items; the agents' text is shown as written, after the code guard. */
"use strict";

const AG = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d }));
  const CAUSES = {
    terrain_blocking: "Hills blocking the signal", distance_to_mast: "Distance to the nearest site",
    single_operator: "Few networks nearby", capacity_strain: "Capacity strain (busy network)", weather: "Weather",
    active_incident: "Active incident", insufficient_data: "Not enough data to tell",
  };
  const AGENTS = { investigator: "Investigator", diagnostician: "Diagnostician", critic: "Critic", advisor: "Advisor",
    fix_planner: "Fix planner", letter_writer: "Letter writer", coordinator: "Coordinator", guard: "Guard" };
  const GUARD_WORDS = {
    number_rewritten: "rewrote a sentence with a number the evidence did not support",
    sentence_removed: "removed a sentence with a number the evidence did not support",
    unknown_citation: "removed a reference to evidence that does not exist",
    dropped_belief: "dropped a cause that no evidence supported",
    weak_cap: "capped a cause at 50% because all its evidence is weak",
    context_only: "stopped earlier diagnoses being used as evidence",
    unexplained_change: "kept a confidence the diagnostician changed without a reason",
    verdict_changed: "turned a request for checks that had already run into a downgrade",
    not_a_candidate: "cleared a fix plan's site that was not a candidate site",
  };
  const word = (c) => (c >= 0.7 ? "likely" : c >= 0.4 ? "possible" : "unlikely");
  const pct = (c) => `${Math.round(c * 100)}%`;
  const causeName = (c) => CAUSES[c] || String(c || "").replace(/_/g, " ");
  const POLL_MS = 1500;
  const TRIM = 150;

  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof out.detail === "string" ? out.detail : JSON.stringify(out.detail || r.statusText));
    return out;
  }

  /* Poll a run until it finishes; onUpdate(run) after every poll. Returns the final run. */
  async function poll(runId, onUpdate, isCurrent = () => true) {
    for (;;) {
      const r = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
      const run = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(run.detail || r.statusText);
      if (!isCurrent()) return null;
      onUpdate(run);
      if (!["queued", "running"].includes(run.status)) return run;
      await new Promise((ok) => setTimeout(ok, POLL_MS));
    }
  }

  const running = (run) => run && ["queued", "running"].includes(run.status);
  const evMap = (run) => Object.fromEntries((run?.evidence || []).map((e) => [e.evidence_id, e]));
  const evNumbers = (run) => Object.fromEntries((run?.evidence || []).map((e, i) => [e.evidence_id, i + 1]));

  /* ---------- text: citations become small numbered references to the Evidence tab ---------- */

  function cite(text, run) {
    const nums = evNumbers(run);
    return esc(text).replace(/(\s*\[[A-Za-z0-9_]+\])+/g, (m) => {
      const ids = [...m.matchAll(/\[([A-Za-z0-9_]+)\]/g)].map((x) => x[1]).filter((i) => nums[i]);
      if (!ids.length) return "";
      return ` <span class="cites">[${ids.map((i) => `<button type="button" class="cite" data-ev="${esc(i)}" title="Evidence ${nums[i]}">${nums[i]}</button>`).join(", ")}]</span>`;
    }).replace(/\s+([.,;:])/g, "$1");
  }

  const plain = (text) => String(text || "").replace(/\s*\[[A-Za-z0-9_]+\]/g, "").replace(/\s+([.,;:])/g, "$1").trim();
  const firstSentence = (text) => (plain(text).match(/^.*?[.!?](?=\s|$)/) || [plain(text)])[0];

  function trimmed(text, run) {
    const p = plain(text);
    if (p.length <= TRIM) return `<span>${cite(text, run)}</span>`;
    return `<details class="more"><summary>${esc(p.slice(0, TRIM).replace(/\s+\S*$/, ""))}… <span class="more-link">more</span></summary>
      <p>${cite(text, run)}</p></details>`;
  }

  /* ---------- progress strip (while running) ---------- */

  function progressHTML(run) {
    const groups = {};
    for (const s of run.steps || []) (groups[s.round || 0] ||= []).push(s);
    const order = Object.keys(groups).map(Number).sort((a, b) => (a || 99) - (b || 99));
    const rows = order.map((r) => {
      const chips = [];
      let tools = null;
      for (const s of groups[r]) {
        if (s.kind === "phase") continue;
        if (s.kind === "tool") {
          if (!tools) { tools = { kind: "tools", steps: [] }; chips.push(tools); }
          tools.steps.push(s);
          continue;
        }
        tools = null;
        chips.push(s);
      }
      return `<div class="pg-row"><span class="pg-round">${r ? `Round ${r}` : "Final"}</span><div class="pg-chips">${chips.map(chipHTML).join("")}</div></div>`;
    });
    const cur = run.current;
    const status = run.status === "queued" ? "Waiting for a free slot"
      : run.status === "running" ? (cur ? `${AGENTS[cur.agent] || cur.agent} is working` : "Starting") : "Finished";
    return `<div class="progress ${running(run) ? "live" : ""}">
      <div class="pg-head"><span class="pg-status">${esc(status)}</span><span class="pg-time">${fmt(run.elapsed_s, 0)} s</span></div>
      ${rows.join("")}</div>`;
  }

  function chipHTML(c) {
    if (c.kind === "tools") {
      const live = c.steps.some((s) => s.status === "running");
      return `<span class="pchip tool ${live ? "running" : "done"}">${c.steps.length} check${c.steps.length > 1 ? "s" : ""}</span>`;
    }
    const name = (AGENTS[c.agent] || c.agent) + (/rewrite/.test(c.label) ? " (correction)" : "");
    return `<span class="pchip ${esc(c.status)}">${esc(name)}</span>`;
  }

  /* ---------- Result tab ---------- */

  function historyHTML(h) {
    return `<ul class="hist">${(h.history || []).map((x, i) => `<li><span class="hist-conf">${x.previous != null ? `${pct(x.previous)} → ` : ""}${pct(x.confidence)}</span>
      <span class="hist-by">${i === 0 ? "first estimate" : `round ${esc(x.round)}, ${esc(AGENTS[x.by] || x.by)}`}</span>
      ${i ? `<span class="hist-why">${esc(plain(String(x.reason || "").replace(/^critic( rejected)?: /, "")))}</span>` : ""}</li>`).join("")}</ul>`;
  }

  function causeHTML(h) {
    const changed = (h.history || []).length > 1;
    return `<li class="why-item">
      <div class="why-head"><strong>${esc(causeName(h.cause))}</strong><span class="muted">${word(h.confidence)}</span></div>
      <div class="cbar" tabindex="0" aria-label="Confidence ${pct(h.confidence)}${changed ? ", revised; focus for the history" : ""}">
        <div class="conf-track"><div class="conf-fill c-${word(h.confidence)}" style="width:${Math.max(3, h.confidence * 100)}%"></div>
          ${changed ? `<div class="conf-first" style="left:${h.history[0].confidence * 100}%"></div>` : ""}</div>
        <span class="conf-num">${pct(h.confidence)}${changed ? " ↺" : ""}</span>
        <div class="conf-pop" role="tooltip"><p class="pop-title">How this confidence changed</p>${historyHTML(h)}</div>
      </div>
      <p>${esc(firstSentence(h.explanation))}</p></li>`;
  }

  /* opts.afterHeadline(run) and opts.afterWhy(run): page-specific blocks (fix card; best network, actions, letter) */
  function resultHTML(run, opts) {
    const r = run.result || {};
    if (running(run)) {
      return `<div class="rp-card">${progressHTML(run)}<p class="muted">The agents are working; this takes one to three minutes.
        <button type="button" class="link" data-tab-link="trace">Follow along</button></p></div>`;
    }
    if (r.status === "failed") return `<p class="error">${esc(r.headline)} ${esc((r.notes || []).join(" "))}</p>`;
    const hyps = r.hypotheses || [];
    const active = hyps.filter((h) => h.status === "active").sort((a, b) => b.confidence - a.confidence);
    const top = active.slice(0, 3);
    const out = [...hyps.filter((h) => h.status === "rejected").map((h) => ({ cause: h.cause, why: (h.history || []).slice(-1)[0]?.reason })),
      ...(r.ruled_out || []).map((x) => ({ cause: x.cause, why: x.reason }))];
    const seen = new Set();
    const ruled = out.filter((x) => !seen.has(x.cause) && seen.add(x.cause));
    return `
      ${r.replayed ? `<p class="recorded-badge" title="Sample mode: no LLM calls were made">Recorded: these agent replies are replayed from
        examples/${esc(r.example_id)} (sample mode)</p>` : ""}
      <p class="rp-headline">${esc(plain(r.headline))}</p>
      ${r.status === "partial" ? `<p class="warn-text">Partial result: an agent failed twice, so this is built from the evidence alone.</p>` : ""}
      ${r.explanation ? `<details class="more-block"><summary>Full explanation</summary><p>${cite(r.explanation, run)}</p></details>` : ""}
      ${opts.afterHeadline ? opts.afterHeadline(run) : ""}
      <h3 class="rp-h">Why we think this</h3>
      <ol class="why">${top.map(causeHTML).join("") || `<li class="muted">No cause stood out.</li>`}</ol>
      ${active.length > 3 ? `<p class="muted">${active.length - 3} other cause${active.length > 4 ? "s" : ""} at ${pct(active[3].confidence)} or less: see How the agents decided.</p>` : ""}
      ${ruled.length ? `<h3 class="rp-h">Ruled out</h3><ul class="ruled">${ruled.map((x) => `<li><strong>${esc(causeName(x.cause))}</strong>
        <span class="muted">${esc(firstSentence(String(x.why || "").replace(/^critic( rejected)?: /, "")))}</span></li>`).join("")}</ul>` : ""}
      ${opts.afterWhy ? opts.afterWhy(run) : ""}
      <p class="muted rp-foot">Confidence is how well the public evidence supports a cause, not proof. Small numbers refer to the Evidence tab.</p>`;
  }

  /* ---------- How the agents decided: a timeline of round cards ---------- */

  function guardHTML(items, tech) {
    if (!items.length) return "";
    const counts = new Map();
    items.forEach((g) => {
      const key = tech ? `${g.kind}: ${g.summary}` : (GUARD_WORDS[g.kind] || g.summary);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return `<li class="tl-step"><span class="tl-who">The guard</span> corrected ${items.length} thing${items.length > 1 ? "s" : ""}
      <ul class="tl-sub">${[...counts].map(([t, n]) => `<li>${esc(t)}${n > 1 ? ` <span class="muted">×${n}</span>` : ""}</li>`).join("")}</ul></li>`;
  }

  function roundCard(c, run, tech, open) {
    const isFinal = !c.round;
    const beliefCrit = c.critiques.filter((k) => k.target !== "fix_plan");
    const fixCrit = c.critiques.filter((k) => k.target === "fix_plan");
    const approved = beliefCrit.filter((k) => k.verdict === "approve");
    const challenged = beliefCrit.filter((k) => k.verdict !== "approve");
    const proposed = c.revisions.filter((v) => v.from == null && v.by === "diagnostician");
    const revised = c.revisions.filter((v) => v.from != null && v.by === "diagnostician");
    const nBeliefs = new Set([...beliefCrit.map((k) => k.target), ...proposed.map((v) => v.belief)]).size;
    const writers = [...new Set(c.llm_calls.map((l) => AGENTS[l.agent] || l.agent))];
    let line = isFinal ? `Final: ${writers.join(" and ") || "the Advisor"} wrote the result`
      : `Round ${c.round}: ${nBeliefs} belief${nBeliefs === 1 ? "" : "s"}, critic approved ${approved.length}, challenged ${challenged.length}`;
    if (fixCrit.length) {
      const last = fixCrit[fixCrit.length - 1];
      line += `; fix plan ${last.verdict === "approve" ? "approved" : "sent back"}${fixCrit.length > 1 ? ` after ${fixCrit.length - 1} revision${fixCrit.length > 2 ? "s" : ""}` : ""}`;
    }
    const steps = [];
    if (c.tools.length) {
      steps.push(`<li class="tl-step"><span class="tl-who">Investigator</span> ran ${c.tools.length} check${c.tools.length > 1 ? "s" : ""}
        ${tech ? `<ul class="tl-sub">${c.tools.map((t) => `<li class="mono">${esc(t.tool)}(${esc(t.args?.operator || "")}) → ${esc((t.evidence_ids || []).join(", "))}</li>`).join("")}</ul>` : ""}</li>`);
    }
    if (proposed.length) {
      steps.push(`<li class="tl-step"><span class="tl-who">Diagnostician</span> ranked ${proposed.length} cause${proposed.length > 1 ? "s" : ""}:
        ${[...proposed].sort((a, b) => b.to - a.to).map((v) => `${esc(causeName(v.belief))} ${pct(v.to)}`).join(", ")}</li>`);
    }
    revised.forEach((v) => steps.push(`<li class="tl-step"><span class="tl-who">Diagnostician</span> revised ${esc(causeName(v.belief))}
      ${pct(v.from)} → ${pct(v.to)}<div class="tl-text">${trimmed(v.reason, run)}</div></li>`));
    if (approved.length) {
      steps.push(`<li class="tl-step"><span class="tl-who">Critic</span> approved ${approved.map((k) => esc(causeName(k.target))).join(", ")}</li>`);
    }
    challenged.forEach((k) => {
      const effect = k.verdict === "downgrade" ? `, lowered to ${pct(k.suggested_confidence)}` : k.verdict === "reject" ? ", rejected"
        : k.requested_checks.length ? `, asked for ${k.requested_checks.length} more check${k.requested_checks.length > 1 ? "s" : ""}` : "";
      steps.push(`<li class="tl-step challenge"><span class="tl-who">Critic</span> challenged ${esc(causeName(k.target))}${effect}
        <div class="tl-text">${trimmed(k.reason, run)}</div></li>`);
    });
    fixCrit.forEach((k) => steps.push(`<li class="tl-step ${k.verdict === "approve" ? "" : "challenge"}"><span class="tl-who">Critic</span>
      ${k.verdict === "approve" ? "approved the fix plan" : "sent the fix plan back"}<div class="tl-text">${trimmed(k.reason, run)}</div></li>`));
    if (isFinal) {
      c.llm_calls.forEach((l) => steps.push(`<li class="tl-step"><span class="tl-who">${esc(AGENTS[l.agent] || l.agent)}</span>
        ${l.agent === "advisor" ? "wrote the explanation from the reviewed causes" : l.agent === "letter_writer" ? "drafted a letter" : "finished"}</li>`));
    }
    steps.push(guardHTML(c.guard, tech));
    const notes = c.coordinator.filter((x) => x.type !== "round");
    if (notes.length) steps.push(`<li class="tl-step"><span class="tl-who">Coordinator</span> ${notes.map((x) => esc(x.summary)).join("; ")}</li>`);
    if (tech) {
      steps.push(`<li class="tl-step tech"><span class="tl-who">Model calls</span><ul class="tl-sub">${c.llm_calls.map((l) =>
        `<li>${esc(AGENTS[l.agent] || l.agent)}${l.purpose ? ` (${esc(l.purpose)})` : ""}: ${esc(l.model)}${l.provider && l.provider !== l.model ? ` via ${esc(l.provider)}` : ""}${l.model_answered && l.model_answered.split("/").pop() !== l.model ? `, answered by ${esc(l.model_answered.split("/").pop())}` : ""}${l.receipt_id ? `, receipt ${esc(String(l.receipt_id).slice(0, 12))}${l.signature_valid === true ? " (signed)" : l.signature_valid === false ? " (signature invalid)" : ""}` : ""}, ${fmt((l.duration_ms || 0) / 1000, 1)} s${l.retries ? `, ${l.retries} retr${l.retries > 1 ? "ies" : "y"}` : ""}${l.fallback ? ", model switched" : ""}${l.error ? ", failed" : ""}</li>`).join("")}</ul></li>`);
    }
    const key = isFinal ? "final" : `r${c.round}`;
    return `<li class="tl-item"><details class="tl-card" data-key="${key}" ${open.has(key) ? "open" : ""}>
      <summary>${esc(line)}</summary><ol class="tl-steps">${steps.join("")}</ol></details></li>`;
  }

  function traceHTML(run, tech, open) {
    if (!run) return `<p class="muted">Run the agents to see how they reach their answer, round by round.</p>`;
    const rounds = run.trace?.rounds || [];
    const calls = rounds.reduce((n, c) => n + c.llm_calls.length, 0);
    const audit = run.trace?.audit || [];
    const rn = run.result?.rounds;
    const rec = run.result?.replayed ? " · recorded replies (sample mode)" : "";
    return `${running(run) ? progressHTML(run) : `<p class="rp-meta">Took ${fmt(run.elapsed_s, 0)} s · ${rn ?? "–"} round${rn === 1 ? "" : "s"} · ${calls} agent calls${rec}</p>`}
      <label class="tech-toggle"><input type="checkbox" id="tech-toggle" ${tech ? "checked" : ""}> Technical details</label>
      ${rounds.length ? `<ol class="timeline">${rounds.map((c) => roundCard(c, run, tech, open)).join("")}</ol>` : `<p class="muted">Waiting for the first round.</p>`}
      <p class="muted rp-foot">From the Meterless audit trail${audit.length ? ` (${audit.length} entries)` : ""}. The agents never message each other:
      each reads and writes this shared case file.${run.memory_mode === "fallback" ? " (In-memory fallback: nothing was saved.)" : ""}</p>`;
  }

  /* ---------- Evidence tab ---------- */

  function evidenceCard(e, n) {
    const size = e.sample_size ? Object.entries(e.sample_size).map(([k, v]) => `${fmt(v, 0)} ${k.replace(/_/g, " ")}`).join(", ") : "";
    const sql = (e.zetaris || []).map((q) => `<pre class="sql">${esc(q.sql)}</pre>`).join("");
    return `<li class="ev-card" id="ev-${esc(e.evidence_id)}">
      <div class="ev-head"><span class="ev-n">${n}</span><p>${esc(plain(e.finding))}</p></div>
      <p class="muted"><span class="strength s-${esc(e.strength)}">${esc(e.strength)}</span> ${esc(e.source)} · ${esc(e.data_period || "–")}${size ? ` · ${esc(size)}` : ""}
        · <span class="ev-id">${esc(e.evidence_id)}</span></p>
      ${sql ? `<details class="ev-sql"><summary>Zetaris query</summary>${sql}</details>` : `<p class="muted">Source: ${esc(e.query_or_url)}</p>`}</li>`;
  }

  function evidenceHTML(run, extra = "") {
    const items = run?.evidence || [];
    return `${items.length ? `<p class="rp-meta">${items.length} evidence items, written to Meterless by the agents' checks.</p>
      <ol class="ev-list">${items.map((e, i) => evidenceCard(e, i + 1)).join("")}</ol>` : `<p class="muted">The agents' evidence appears here once they run.</p>`}
      ${extra}
      ${run?.world?.entities?.length ? `<h3 class="rp-h">World Model</h3>${worldHTML(run.world, run.scope === "zone" ? "zone" : "place")}` : ""}`;
  }

  /* ---------- World Model: the place or zone and what it is linked to ---------- */

  function worldHTML(world, centreType) {
    const ents = world?.entities || [], rels = world?.relationships || [];
    const byId = Object.fromEntries(ents.map((e) => [e.id, e]));
    const centre = ents.find((e) => e.type === centreType) || ents[0];
    if (!centre) return "";
    const W = 408, H = 320, cx = W / 2, cy = H / 2;
    const first = rels.filter((r) => r.from === centre.id && byId[r.to]);
    const ring = [...new Set(first.map((r) => r.to))];
    const pos = { [centre.id]: [cx, cy] };
    ring.forEach((id, i) => {
      const a = (i / Math.max(ring.length, 1)) * 2 * Math.PI - Math.PI / 2;
      pos[id] = [cx + 112 * Math.cos(a), cy + 88 * Math.sin(a)];
    });
    const second = rels.filter((r) => pos[r.from] && r.from !== centre.id && byId[r.to] && !pos[r.to]);
    const outer = [...new Set(second.map((r) => r.to))];
    outer.forEach((id, i) => {
      const a = (i / Math.max(outer.length, 1)) * 2 * Math.PI - Math.PI / 4;
      pos[id] = [cx + 168 * Math.cos(a), cy + 130 * Math.sin(a)];
    });
    const label = (e) => esc((e.name || e.id.split(":").slice(2).join(":")).slice(0, 26));
    const color = { place: "#15191F", zone: "#15191F", site: "#1D4ED8", candidate_site: "#7C3AED", operator: "#0D9488", area: "#64748B", warning: "#C2410C" };
    const edges = [...first, ...second.filter((r) => pos[r.to])].map((r) => {
      const [x1, y1] = pos[r.from], [x2, y2] = pos[r.to];
      return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="wm-edge ${r.attrs?.line_of_sight === "blocked" ? "blocked" : ""}"><title>${esc(r.type.replace(/_/g, " "))}${r.attrs?.operator ? ` (${esc(r.attrs.operator)})` : ""}${r.attrs?.line_of_sight ? `: line of sight ${esc(r.attrs.line_of_sight)}` : ""}</title></line>`;
    }).join("");
    const nodes = Object.entries(pos).map(([id, [x, y]]) => {
      const e = byId[id], big = id === centre.id;
      return `<g class="wm-node"><circle cx="${x}" cy="${y}" r="${big ? 10 : 7}" fill="${color[e.type] || "#94A3B8"}"><title>${esc(e.type.replace(/_/g, " "))}: ${esc(e.name || id)}</title></circle>
        <text x="${x}" y="${y + (big ? 24 : 20)}" text-anchor="middle">${label(e)}</text></g>`;
    }).join("");
    const counts = {};
    ents.forEach((e) => { counts[e.type] = (counts[e.type] || 0) + 1; });
    return `<svg class="wm" viewBox="0 0 ${W} ${H}" role="img" aria-label="World Model entities linked to this ${esc(centre.type)}">${edges}${nodes}</svg>
      <p class="muted">${Object.entries(counts).map(([t, n]) => `${n} ${esc(t.replace("_", " "))}${n > 1 ? "s" : ""}`).join(" · ")} · ${rels.length} relationships.
      Red dashed: line of sight blocked. Hover a point or line for details.</p>`;
  }

  /* ---------- the tabbed view ---------- */

  const TABS = [["result", "Result"], ["trace", "How the agents decided"], ["evidence", "Evidence"]];

  /* tabsEl and bodyEl may sit apart (Council view: tabs in the sticky header). opts: idle() (Result before any
     run), afterHeadline(run), afterWhy(run), evidenceExtra(run), onRender(view), scroller (element to reset). */
  function runView(tabsEl, bodyEl, opts = {}) {
    const v = { run: null, tab: "result", tech: false, open: new Set() };
    tabsEl.innerHTML = `<div class="rp-tabs" role="tablist">${TABS.map(([k, label]) =>
      `<button type="button" role="tab" data-tab="${k}">${label}</button>`).join("")}</div>`;

    v.render = () => {
      tabsEl.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === v.tab)));
      if (v.tab === "result") bodyEl.innerHTML = v.run ? resultHTML(v.run, opts) : (opts.idle ? opts.idle() : "");
      else if (v.tab === "trace") bodyEl.innerHTML = traceHTML(v.run, v.tech, v.open);
      else bodyEl.innerHTML = evidenceHTML(v.run, opts.evidenceExtra ? opts.evidenceExtra(v.run) : "");
      if (opts.onRender) opts.onRender(v);
    };
    v.setTab = (t) => {
      v.tab = t;
      v.render();
      const s = opts.scroller ? opts.scroller() : bodyEl;
      if (s) s.scrollTop = opts.scrollTop ? opts.scrollTop() : 0;
    };
    v.setRun = (run) => { v.run = run; v.render(); };
    v.reset = () => { v.run = null; v.tab = "result"; v.open = new Set(); v.render(); };

    tabsEl.addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) v.setTab(b.dataset.tab); });
    bodyEl.addEventListener("click", (e) => {
      const link = e.target.closest("[data-tab-link]");
      if (link) { v.setTab(link.dataset.tabLink); return; }
      const c = e.target.closest(".cite");
      if (!c) return;
      e.preventDefault();
      v.setTab("evidence");
      const card = bodyEl.querySelector(`#ev-${CSS.escape(c.dataset.ev)}`);
      if (card) {
        card.classList.add("hl");
        const sql = card.querySelector("details");
        if (sql) sql.open = true;
        card.scrollIntoView({ block: "start" });
      }
    });
    bodyEl.addEventListener("change", (e) => { if (e.target.id === "tech-toggle") { v.tech = e.target.checked; v.render(); } });
    bodyEl.addEventListener("toggle", (e) => {
      const d = e.target;
      if (d.classList?.contains("tl-card")) (d.open ? v.open.add(d.dataset.key) : v.open.delete(d.dataset.key));
    }, true);
    v.render();
    return v;
  }

  /* ---------- Markdown (letters, summaries): a small safe subset ---------- */

  function markdown(md) {
    const inline = (s) => esc(s).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    const out = [];
    let list = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const line of String(md || "").split("\n")) {
      const h = line.match(/^(#{1,4})\s+(.*)/), li = line.match(/^\s*[-*]\s+(.*)/), ol = line.match(/^\s*\d+\.\s+(.*)/);
      if (h) { close(); out.push(`<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`); }
      else if (li || ol) {
        const kind = li ? "ul" : "ol";
        if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
        out.push(`<li>${inline((li || ol)[1])}</li>`);
      } else if (/^-{3,}$/.test(line.trim())) { close(); out.push("<hr>"); }
      else if (line.trim()) { close(); out.push(`<p>${inline(line)}</p>`); }
      else close();
    }
    close();
    return out.join("");
  }

  function download(name, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  /* Sample mode: a banner on every page (data from committed extracts, recorded agent replies). */
  fetch("/health").then((r) => r.json()).then((h) => {
    if (!h.sample_mode) return;
    const b = document.createElement("div");
    b.className = "sample-banner";
    b.textContent = "Sample mode: data from small committed extracts (DuckDB in place of Zetaris), saved live responses and "
      + "recorded agent replies. Only the example places and zones can be diagnosed.";
    document.body.prepend(b);
  }).catch(() => {});

  return { esc, fmt, CAUSES, causeName, word, pct, post, poll, running, cite, plain, firstSentence, progressHTML, runView,
    markdown, download, evMap };
})();
