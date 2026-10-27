/* Council view: gap zones and improvement opportunities on the map now (GET /api/council/view; zoom caps in
   config.COUNCIL_VIEW), ranked by a transparent score with adjustable weights; a zone panel with the agents'
   diagnosis (tabs from agents.js) and a fix view (candidate sites, cell outcomes, Today / With candidate). */
"use strict";

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const UK_VIEW = { center: [-3.2, 54.6], zoom: 4.9 };
const GAP_FACTORS = {
  people: { label: "People affected", color: "#1D4ED8" },
  severity: { label: "Severity", color: "#C2410C" },
  persistence: { label: "Persistence", color: "#7C3AED" },
  trend: { label: "Worsening trend", color: "#0D9488" },
  operator_choice: { label: "Few operators", color: "#CA8A04" },
};
const OPP_FACTORS = {
  people: { label: "People affected", color: "#1D4ED8" },
  shortfall: { label: "Shortfall vs median", color: "#0F766E" },
  trend: { label: "Worsening trend", color: "#7C3AED" },
  operator_choice: { label: "Operator choice", color: "#CA8A04" },
};
const DEFAULT_WEIGHTS = { people: 0.35, severity: 0.25, persistence: 0.15, trend: 0.10, operator_choice: 0.15 };
const DEFAULT_OPP_WEIGHTS = { people: 0.35, shortfall: 0.30, trend: 0.15, operator_choice: 0.20 };
const GAP_SCALE = { breaks: [40, 55, 70, 85], colors: ["#FEF3C7", "#FDBA74", "#F97316", "#C2410C", "#7C2D12"] };
const OPP_SCALE = { breaks: [40, 55, 70, 85], colors: ["#CCFBF1", "#5EEAD4", "#14B8A6", "#0F766E", "#134E4A"] };
const OP_COLORS = { EE: "#0D9488", O2: "#2563EB", Vodafone: "#9333EA", Three: "#CA8A04" };
const REASON_TAGS = { slow: "Slow vs median", falling: "Speeds falling", few_operators: "1-2 operators", capacity: "Busy network",
  no_site: "No site nearby", blocked: "Hills block sites" };
const FIX_LABELS = { new_site: "A new site", add_operators: "Add operators at an existing site", capacity_upgrade: "A capacity upgrade",
  gather_data: "Measure first (gather data)" };
const OPP_ZOOM = 10;          // opportunities are listed from this zoom (config.COUNCIL_VIEW)
const SITE_AREA_M = 250;      // dashed circle around a candidate: the approximate site area
const MAX_REACH_LINES = 12;
const GREEN = "#16A34A", BLOCKED_GREY = "#64748B", MUTED = "#CBD5E1", LINE = "#15803D";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: 0 }));
const rgb = (hex, a = 255) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16), a];
const scaleColor = (scale, s) => scale.colors[scale.breaks.filter((b) => s >= b).length];
const isOpp = (id) => String(id || "").startsWith("O");

const state = {
  scope: "uk", pick: "view", show: "both", weights: { ...DEFAULT_WEIGHTS }, oppWeights: { ...DEFAULT_OPP_WEIGHTS }, wKind: "gap",
  view: null, request: 0, zone: null, detail: null, run: null, runId: null, panel: null, prevView: null, queue: null,
  fix: { cand: null, chosen: false, mode: "today", showAll: false, hover: null },
};

async function getJSON(url) {
  const r = await fetch(url);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status} ${r.statusText}`);
  return body;
}

/* ---------- map ---------- */

const map = new maplibregl.Map({ container: "map", style: BASEMAP, ...UK_VIEW, attributionControl: { compact: true } });
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
const overlay = new deck.MapboxOverlay({ interleaved: false, layers: [], getCursor: ({ isHovering }) => (isHovering ? "pointer" : "grab") });
map.addControl(overlay);
const dashed = new deck.PathStyleExtension({ dash: true });
/* Diagonal hatch lines inside a (convex) hexagon, about every 180 m: "still blocked". */
function hatchLines(poly, spacingM = 180) {
  const lat0 = poly[0][1], kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 111320;
  const pts = poly.map(([lon, lat]) => [lon * kx, lat * ky]);
  const proj = pts.map(([x, y]) => x - y);  // lines x - y = c
  const lines = [];
  for (let c = Math.ceil(Math.min(...proj) / spacingM) * spacingM; c < Math.max(...proj); c += spacingM) {
    const hits = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i], [x2, y2] = pts[i + 1], d1 = x1 - y1 - c, d2 = x2 - y2 - c;
      if ((d1 <= 0 && d2 > 0) || (d1 > 0 && d2 <= 0)) {
        const t = d1 / (d1 - d2);
        hits.push([(x1 + t * (x2 - x1)) / kx, (y1 + t * (y2 - y1)) / ky]);
      }
    }
    if (hits.length >= 2) lines.push([hits[0], hits[1]]);
  }
  return lines;
}
const pinIcon = (color) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="36" height="46" viewBox="0 0 36 46">
  <path d="M18 45 C18 45 3 27 3 17 A15 15 0 0 1 33 17 C33 27 18 45 18 45 Z" fill="${color}" stroke="#fff" stroke-width="3"/></svg>`)}`;

function circle(lat, lon, m, n = 48) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * 2 * Math.PI;
    pts.push([lon + (m / (111320 * Math.cos((lat * Math.PI) / 180))) * Math.sin(a), lat + (m / 111320) * Math.cos(a)]);
  }
  return pts;
}
const distM = (a, b) => {
  const r = Math.PI / 180, x = (b.lon - a.lon) * Math.cos(((a.lat + b.lat) / 2) * r), y = b.lat - a.lat;
  return Math.hypot(x, y) * 111320;
};

/* The selected candidate: the user's choice, else the agents' pick, else candidate 1. */
function agentPickIndex() {
  const f = state.run?.result?.fix_plan;
  if (!f || !f.candidate || state.run.result.zone?.zone_id !== state.zone) return null;
  const n = Number(String(f.candidate).replace("cand_", ""));
  return Number.isFinite(n) ? n - 1 : null;
}
function selectedCandidate() {
  const d = state.detail;
  if (!d || !d.candidates.length) return null;
  const i = Math.min(state.fix.chosen ? state.fix.cand : (agentPickIndex() ?? 0), d.candidates.length - 1);
  return { ...d.candidates[i], index: i };
}

/* Each zone cell's outcome for a candidate: gains a clear line, still blocked (checked, no line), or not tested. */
function outcomes(cand) {
  const reached = new Set((cand?.reached || []).filter((r) => r.in_zone).map((r) => r.cell_id));
  return (state.detail?.cells || []).map((c) => ({ ...c,
    outcome: !cand ? "today" : reached.has(c.cell_id) ? "gains" : (cand.cells_checked ? "blocked" : "untested") }));
}

function todayColor(c) {
  if (isOpp(state.zone)) return rgb("#14B8A6", 90);
  return rgb(c.no_site ? "#C2410C" : c.blocked ? "#F97316" : "#F59E0B", 90);
}

function zoneLayers() {
  const v = state.view;
  if (!v) return [];
  const sel = state.zone;
  const out = [];
  for (const [key, scale, id] of [["gaps", GAP_SCALE, "gap"], ["opportunities", OPP_SCALE, "opp"]]) {
    const zones = v[key]?.zones || [];
    const cells = zones.filter((z) => z.zone_id !== sel || !state.detail).flatMap((z) => z.cell_ids.map((c) => ({ cell: c, zone: z })));
    out.push(new deck.H3HexagonLayer({
      id: `${id}-zones`, data: cells, getHexagon: (d) => d.cell, extruded: false, stroked: false, coverage: 1,
      getFillColor: (d) => rgb(scaleColor(scale, d.zone.score), sel ? 110 : 215), pickable: true,
      updateTriggers: { getFillColor: [sel] },
    }));
    out.push(new deck.TextLayer({
      id: `${id}-ranks`, data: zones.slice(0, 40).filter((z) => z.zone_id !== sel || !state.detail), getPosition: (z) => [z.centroid.lon, z.centroid.lat],
      getText: (z) => String(z.pos), getSize: 12, getColor: rgb(id === "gap" ? "#15191F" : "#134E4A"), fontWeight: 800,
      fontFamily: "Manrope, sans-serif", outlineWidth: 4, outlineColor: [255, 255, 255, 240], fontSettings: { sdf: true },
      getPixelOffset: [0, -14],
    }));
  }
  return out;
}

/* The fix view, bottom to top: cells, reach overlay, existing sites, candidate circles, pins, labels. */
function fixLayers() {
  const d = state.detail;
  if (!d) return [];
  const cand = selectedCandidate();
  const withCand = state.fix.mode === "with" && cand;
  const cells = outcomes(withCand ? cand : null);
  const pick = agentPickIndex();
  const out = [];
  out.push(new deck.H3HexagonLayer({
    id: "fix-cells", data: cells, getHexagon: (c) => c.cell_id, extruded: false, coverage: 1, stroked: true,
    getLineColor: [21, 25, 31, 150], lineWidthUnits: "pixels", getLineWidth: 1, pickable: true,
    getFillColor: (c) => (c.outcome === "today" ? todayColor(c) : c.outcome === "gains" ? rgb(GREEN, 150)
      : c.outcome === "blocked" ? rgb("#E2E8F0", 200) : rgb(MUTED, 70)),
    updateTriggers: { getFillColor: [state.fix.mode, cand?.index, state.zone] },
  }));
  if (withCand) {
    out.push(new deck.PathLayer({
      id: "fix-hatch", data: cells.filter((c) => c.outcome === "blocked").flatMap((c) => hatchLines(h3.cellToBoundary(c.cell_id, true))),
      getPath: (l) => l, getColor: rgb(BLOCKED_GREY, 200), getWidth: 1.2, widthUnits: "pixels",
    }));
  }
  // Reach overlay: the selected candidate only, to the nearest 12 cells that gain a line; hovering a cell adds its line.
  if (cand && !state.fix.showAll) {
    const gains = (withCand ? cand.reached.filter((r) => r.in_zone) : [])
      .map((r) => ({ ...r, dist: distM(cand, r) })).sort((a, b) => a.dist - b.dist).slice(0, MAX_REACH_LINES);
    const hov = state.fix.hover && cand.reached.find((r) => r.in_zone && r.cell_id === state.fix.hover);
    if (hov && !gains.some((g) => g.cell_id === hov.cell_id)) gains.push({ ...hov, hover: true });
    out.push(new deck.LineLayer({
      id: "reach", data: gains, getSourcePosition: () => [cand.lon, cand.lat], getTargetPosition: (r) => [r.lon, r.lat],
      getColor: (r) => rgb(LINE, r.hover || r.cell_id === state.fix.hover ? 255 : 170),
      getWidth: (r) => (r.hover || r.cell_id === state.fix.hover ? 2.5 : 1.2), widthUnits: "pixels",
      updateTriggers: { getColor: [state.fix.hover], getWidth: [state.fix.hover] },
    }));
  }
  const nearZone = (s) => d.cells.some((c) => distM(c, s) < 6000);
  out.push(new deck.ScatterplotLayer({
    id: "existing", data: (d.existing_all || []).filter(nearZone), getPosition: (s) => [s.lon, s.lat],
    getFillColor: rgb("#94A3B8"), stroked: true, lineWidthUnits: "pixels", getLineWidth: 2.5, radiusUnits: "pixels", getRadius: 4,
    getLineColor: (s) => rgb(s.operators.length === 1 ? OP_COLORS[s.operators[0]] || "#94A3B8" : s.operators.length ? "#334155" : "#E2E8F0"),
    pickable: true,
  }));
  const shown = state.fix.showAll ? d.candidates.map((c, i) => ({ ...c, index: i })) : cand ? [cand] : [];
  out.push(new deck.PathLayer({
    id: "site-areas", data: shown.map((c) => ({ ...c, path: circle(c.lat, c.lon, SITE_AREA_M) })), getPath: (c) => c.path,
    getColor: rgb("#15191F", 200), getWidth: 1.5, widthUnits: "pixels", getDashArray: [4, 3], dashJustified: true, extensions: [dashed],
  }));
  out.push(new deck.IconLayer({
    id: "pins", data: shown, getPosition: (c) => [c.lon, c.lat], pickable: true, sizeUnits: "pixels", getSize: 40,
    getIcon: (c) => ({ url: pinIcon(c.index === pick ? "#1D4ED8" : "#334155"), width: 36, height: 46, anchorY: 46 }),
    updateTriggers: { getIcon: [pick] },
  }));
  out.push(new deck.TextLayer({
    id: "pin-numbers", data: shown, getPosition: (c) => [c.lon, c.lat], getText: (c) => String(c.index + 1), getSize: 14,
    getPixelOffset: [0, -26], getColor: [255, 255, 255, 255], fontWeight: 800, fontFamily: "Manrope, sans-serif", fontSettings: { sdf: true },
  }));
  out.push(new deck.TextLayer({
    id: "pin-stars", data: shown.filter((c) => c.index === pick), getPosition: (c) => [c.lon, c.lat], getText: () => "★", getSize: 18,
    getPixelOffset: [15, -42], getColor: rgb("#F59E0B"), outlineWidth: 3, outlineColor: [255, 255, 255, 255], fontSettings: { sdf: true },
    characterSet: ["★"],
  }));
  out.push(new deck.TextLayer({
    // With all candidates shown, labels are stacked by number so nearby pins do not overlap them.
    id: "pin-labels", data: shown, getPosition: (c) => [c.lon, c.lat], getSize: 13,
    getPixelOffset: (c) => [0, -62 - (state.fix.showAll ? c.index * 28 : 0)], updateTriggers: { getPixelOffset: [state.fix.showAll] },
    getText: (c) => `${c.index + 1}${c.index === pick ? " · Recommended" : ""} · reaches ${fmt(c.people_reached, 0)}`,
    getColor: [21, 25, 31, 255], fontWeight: 700, fontFamily: "Manrope, sans-serif", background: true,
    getBackgroundColor: [255, 255, 255, 240], backgroundPadding: [8, 4], getBorderColor: [203, 213, 225, 255], getBorderWidth: 1,
    characterSet: "auto",
  }));
  return out;
}

function layers() {
  const out = zoneLayers();
  const d = state.detail;
  if (d) {
    out.push(new deck.PolygonLayer({
      id: "zone-outline", data: d.zone.cell_ids.map((c) => h3.cellToBoundary(c, true)), getPolygon: (p) => p, filled: false,
      stroked: true, getLineColor: [21, 25, 31, 255], lineWidthUnits: "pixels", getLineWidth: 2,
    }));
    out.push(...fixLayers());
  }
  return out;
}

function tooltip({ object, layer }) {
  if (!object) return null;
  if (layer.id === "gap-zones" || layer.id === "opp-zones") {
    const z = object.zone, opp = layer.id === "opp-zones";
    return { html: `<strong>${opp ? "Opportunity" : "Gap zone"} ${z.pos}: ${esc(z.name)}</strong><br>${esc(z.council)} · ${fmt(z.people, 0)} people<br>`
      + `${opp ? "Opportunity" : "Priority"} score ${fmt(z.score, 1)} (#${z.rank} in ${esc(scopeName(state.scope))})${opp ? `<br>${z.reasons.map((r) => REASON_TAGS[r]).join(", ")}` : ""}` };
  }
  if (layer.id === "fix-cells") {
    const words = { today: isOpp(state.zone) ? "cell with a measurable weakness" : "poorly served cell", gains: "gains a clear line",
      blocked: "still blocked", untested: "not tested" };
    return { html: `<strong>${fmt(object.population, 0)} people</strong><br>${words[object.outcome]}` };
  }
  if (layer.id === "pins") {
    return { html: `<strong>Candidate ${object.index + 1}</strong>: new site on high ground (${fmt(object.ground_m, 0)} m)<br>`
      + `reaches ${fmt(object.people_reached, 0)} people; ${fmt(object.zone_people_gaining, 0)} in this zone` };
  }
  if (layer.id === "existing") {
    return { html: `<strong>Existing site</strong><br>${esc(object.operators.join(", ") || "operator unknown")}` };
  }
  return null;
}

function render() {
  overlay.setProps({
    layers: layers(), getTooltip: tooltip,
    onClick: (info) => {
      if (info.object?.zone) openZone(info.object.zone.zone_id);
      else if (info.layer?.id === "pins") selectCandidate(info.object.index, false);
    },
    onHover: (info) => {
      const id = info.layer?.id === "fix-cells" ? info.object?.cell_id : null;
      if ((id || null) !== state.fix.hover) { state.fix.hover = id || null; render(); }
    },
  });
  renderFixSwitch();
}

function legendScale(title, units, scale) {
  return `<h2>${title}</h2><p class="units">${units}</p><ul class="scale">
    ${scale.colors.map((c, i) => `<li><span class="swatch" style="background:${c}"></span>${["< 40", "40 – 54", "55 – 69", "70 – 84", "85 +"][i]}</li>`).join("")}</ul>`;
}

function renderLegend() {
  const v = state.view, d = state.detail;
  const gaps = state.show !== "opportunities", opps = state.show !== "gaps" && (v?.opportunities?.shown || 0) > 0;
  let html = "";
  if (gaps) html += legendScale("Gap zones", "Priority score, 0 – 100", GAP_SCALE);
  if (opps) html += legendScale("Improvement opportunities", "Opportunity score, 0 – 100", OPP_SCALE);
  if (d) {
    const opp = isOpp(state.zone);
    html += `<h2 class="legend-sub">Selected zone</h2><ul>
      ${state.fix.mode === "with" ? `<li><span class="swatch" style="background:${GREEN}"></span>Gains a clear line</li>
        <li><span class="swatch hatch"></span>Still blocked</li><li><span class="swatch" style="background:${MUTED}"></span>Not tested</li>`
        : opp ? `<li><span class="swatch" style="background:#99F6E4"></span>Cell with a measurable weakness</li>`
        : `<li><span class="swatch" style="background:#FDBA74"></span>Poorly served cell (today)</li>`}
      <li><span class="pin-swatch"></span>Candidate site (number); <span class="star">★</span> the agents' recommendation</li>
      <li><span class="ring-swatch"></span>Approximate site area (about 250 m)</li>
      <li><span class="dot existing-dot"></span>Existing site (ring: operator)</li></ul>`;
  } else {
    html += `<p class="note">Numbers: position in the list. Select a zone to see its fix.</p>`;
  }
  $("legend").innerHTML = html;
}

/* Today / With candidate N, with the people counts on both. */
function renderFixSwitch() {
  const el = $("fix-switch"), d = state.detail;
  const cand = selectedCandidate();
  if (!d || !cand) { el.hidden = true; return; }
  const opp = isOpp(state.zone);
  const total = Math.round(d.cells.reduce((n, c) => n + c.population, 0));
  const gains = Math.round(cand.reached.filter((r) => r.in_zone).reduce((n, r) => n + r.population, 0));
  el.hidden = false;
  el.innerHTML = `<div class="seg" role="radiogroup" aria-label="Before and after">
      <button type="button" role="radio" data-mode="today" aria-checked="${state.fix.mode === "today"}">Today
        <span>${fmt(total, 0)} people in ${opp ? "cells with a weakness" : "poorly served cells"}</span></button>
      <button type="button" role="radio" data-mode="with" aria-checked="${state.fix.mode === "with"}">With candidate ${cand.index + 1}
        <span>${fmt(gains, 0)} gain a clear line · ${fmt(Math.max(0, total - gains), 0)} still blocked</span></button></div>
    <label class="map-toggle"><input type="checkbox" id="show-all" ${state.fix.showAll ? "checked" : ""}> Show all candidates</label>`;
  el.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => { state.fix.mode = b.dataset.mode; render(); renderLegend(); }));
  $("show-all").addEventListener("change", (e) => { state.fix.showAll = e.target.checked; render(); });
}

function selectCandidate(i, focus = true) {
  state.fix.cand = i;
  state.fix.chosen = true;
  state.fix.mode = "with";
  render();
  renderLegend();
  view.render();
  if (focus) fitZone(true);
}

/* ---------- weights ---------- */

function renderSliders() {
  const opp = state.wKind === "opportunity";
  const factors = opp ? OPP_FACTORS : GAP_FACTORS, w = opp ? state.oppWeights : state.weights;
  document.querySelectorAll("#weights-seg [data-w]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.w === state.wKind)));
  $("sliders").innerHTML = Object.entries(factors).map(([k, f]) => `<label class="slider"><span class="f-dot" style="background:${f.color}"></span>
      <span class="f-name">${f.label}</span>
      <input type="range" min="0" max="1" step="0.05" value="${w[k]}" data-k="${k}" aria-label="${f.label} weight">
      <span class="f-val" id="w-${k}">${Math.round(w[k] * 100)}</span></label>`).join("");
  $("sliders").querySelectorAll("input").forEach((el) => el.addEventListener("input", () => {
    w[el.dataset.k] = +el.value;
    $(`w-${el.dataset.k}`).textContent = Math.round(+el.value * 100);
    clearTimeout(state.timer);
    state.timer = setTimeout(loadView, 250);
  }));
  $("weights-note").textContent = opp
    ? "Opportunity score: the same transparent method, for areas that are not poorly served but have a measurable weakness. Weights are rescaled to 100%."
    : "Weights are rescaled to add up to 100%. Data confidence (Ookla tests) is shown beside each zone, not scored.";
  syncCsv();
}

const weightParam = () => Object.entries(state.weights).map(([k, v]) => `${k}:${v}`).join(",");
const oppWeightParam = () => Object.entries(state.oppWeights).map(([k, v]) => `${k}:${v}`).join(",");
function syncCsv() {
  const opp = state.wKind === "opportunity";
  $("csv").href = `/api/council/zones.csv?scope=${encodeURIComponent(state.scope)}&type=${opp ? "opportunity" : "gap"}`
    + `&weights=${encodeURIComponent(opp ? oppWeightParam() : weightParam())}`;
}

/* ---------- the list: always the zones on the map now ---------- */

function bar(breakdown, opp) {
  const F = opp ? OPP_FACTORS : GAP_FACTORS;
  return `<div class="score-bar" title="${esc(Object.entries(breakdown || {}).map(([k, b]) => `${F[k]?.label || k}: ${b.contribution}`).join("\n"))}">
    ${Object.entries(breakdown || {}).map(([k, b]) => `<span style="width:${b.contribution}%;background:${F[k]?.color || "#94A3B8"}"></span>`).join("")}</div>`;
}

const DATA_WORDS = {
  low: "Low data confidence: fewer than 10 Ookla tests in the zone, so the ranking is an estimate. Measure first.",
  medium: "Medium data confidence: 10 to 49 Ookla tests in the zone.",
  high: "Good data: 50 or more Ookla tests in the zone.",
};
const dataDot = (z) => (z.zone_type === "opportunity" && z.limited_data
  ? `<span class="dq dq-low" title="Limited data: fewer than 10 Ookla tests (${fmt(z.tests, 0)})"></span><span class="dq-limited">limited data</span>`
  : `<span class="dq dq-${z.confidence}" title="${esc(DATA_WORDS[z.confidence] || "")} (${fmt(z.tests, 0)} tests)"
    aria-label="${esc(DATA_WORDS[z.confidence] || "")}"></span>${z.confidence === "high" ? `<span class="dq-label">good data</span>` : ""}`);

function rowHTML(z) {
  const opp = z.zone_type === "opportunity";
  return `<tr data-zone="${z.zone_id}" class="${opp ? "opp-row" : ""} ${z.zone_id === state.zone ? "selected" : ""}" tabindex="0">
    <td class="rank" title="#${z.rank} in ${esc(scopeName(state.scope))}">${z.pos}</td>
    <td class="zone-cell"><span class="zone-name"><span class="zn">${esc(z.name)}</span>${dataDot(z)}</span>
      <span class="zone-sub">${z.zone_id} · ${esc(z.council)}</span>
      ${opp ? `<span class="reason-tags">${z.reasons.map((r) => `<span class="cause-tag">${REASON_TAGS[r]}</span>`).join("")}</span>` : ""}</td>
    <td class="num">${fmt(z.people, 0)}</td>
    <td class="num">${z.median_speed_mbps === null ? "–" : fmt(z.median_speed_mbps, 1)}<span class="zone-sub">${fmt(z.tests, 0)} test${z.tests === 1 ? "" : "s"}</span></td>
    <td class="score-cell"><strong>${fmt(z.score, 1)}</strong>${bar(z.breakdown, opp)}</td>
    <td class="act"><button type="button" class="row-diagnose" data-diagnose="${z.zone_id}">Diagnose</button></td></tr>`;
}

function sectionHTML(title, part, hiddenNote) {
  const head = `<thead><tr><th>#</th><th>${title}</th><th class="num" title="People in the zone's cells">People</th>
    <th class="num" title="Median download speed of the zone's cells (Mbps), latest quarter; tests below">Mbps</th>
    <th title="Score, 0 to 100 within the chosen area">Score</th><th></th></tr></thead>`;
  const rows = part.zones.length ? part.zones.map(rowHTML).join("") : `<tr><td colspan="6" class="empty">${hiddenNote || "None in view."}</td></tr>`;
  return `<table class="zones"><colgroup><col class="c-rank"><col><col class="c-num"><col class="c-num"><col class="c-score"><col class="c-act"></colgroup>
    ${head}<tbody>${rows}</tbody></table>`;
}

function renderList() {
  const v = state.view;
  if (!v) return;
  const parts = [];
  const g = v.gaps, o = v.opportunities;
  const summary = [];
  if (state.show !== "opportunities") {
    summary.push(`<strong>${fmt(g.in_view, 0)} gap zone${g.in_view === 1 ? "" : "s"}</strong> in view (${fmt(g.people_in_view || 0, 0)} people)${g.shown < g.in_view ? `, top ${g.shown} listed` : ""}`);
    parts.push(sectionHTML("Gap zones", g));
  }
  if (state.show !== "gaps") {
    const zoomedOut = o.hidden === "zoom";
    summary.push(`<strong>${fmt(o.in_view, 0)} improvement opportunit${o.in_view === 1 ? "y" : "ies"}</strong> in view${zoomedOut ? " (zoom in to list them)" : o.shown < o.in_view ? `, top ${o.shown} listed` : ""}`);
    parts.push(sectionHTML("Improvement opportunities", o, zoomedOut ? `Zoom in to level ${OPP_ZOOM} or closer to list improvement opportunities.` : ""));
  }
  $("list-summary").innerHTML = `${summary.join(" · ")}. <span class="muted">${esc(pickLabel())}; ${v.duration_ms} ms through Zetaris.</span>`;
  $("list").innerHTML = parts.join("");
  $("list").querySelectorAll("tr[data-zone]").forEach((tr) => {
    tr.addEventListener("click", (e) => { if (!e.target.closest("button")) openZone(tr.dataset.zone); });
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openZone(tr.dataset.zone); });
  });
  $("list").querySelectorAll("[data-diagnose]").forEach((b) => b.addEventListener("click", () => openZone(b.dataset.diagnose, null, true)));
  const notes = v.notes || [];
  $("map-note").hidden = !notes.length;
  $("map-note").innerHTML = notes.map(esc).join("<br>");
  const tog = $("opp-toggle");
  tog.checked = state.show !== "gaps";
  tog.disabled = map.getZoom() < OPP_ZOOM;
  $("opp-toggle-wrap").title = tog.disabled ? `Zoom in to level ${OPP_ZOOM} to see improvement opportunities` : "";
  $("top3-btn").disabled = !listedZones().length || !!state.queue;
}

const listedZones = () => [...(state.show !== "opportunities" ? state.view?.gaps.zones || [] : []),
  ...(state.show !== "gaps" ? state.view?.opportunities.zones || [] : [])];

async function loadView() {
  const id = ++state.request;
  const b = map.getBounds();
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((x) => x.toFixed(4)).join(",");
  const qs = new URLSearchParams({ bbox, zoom: map.getZoom().toFixed(2), scope: state.scope, show: state.show,
    weights: weightParam(), opp_weights: oppWeightParam(), ...(state.zone ? { selected: state.zone } : {}) });
  try {
    const v = await getJSON(`/api/council/view?${qs}`);
    if (id !== state.request) return;
    // Rows and map labels are numbered by position in view; the rank within the area is in the tooltip.
    for (const part of [v.gaps, v.opportunities]) (part.zones || []).forEach((z, i) => { z.pos = i + 1; });
    state.view = v;
    renderList();
    renderLegend();
    render();
  } catch (e) {
    $("list-summary").innerHTML = `<span class="error">Could not load zones: ${esc(e.message)}</span>`;
  }
}
let moveTimer = null;
map.on("moveend", () => { clearTimeout(moveTimer); moveTimer = setTimeout(loadView, 300); });

/* ---------- area picker (combobox) and Gaps | Opportunities | Both ---------- */

let SCOPES = null;
const scopeName = (s) => (s === "uk" ? "United Kingdom" : s.startsWith("nation:") ? s.slice(7)
  : (SCOPES?.councils.find((c) => c.scope === s) || {}).name || s);
const pickLabel = () => (state.pick === "uk" ? "Whole UK (top 40 gap zones)" : state.pick === "view" ? "What's on the map now"
  : scopeName(state.pick));

function areaOptions() {
  const count = (x) => `${fmt(x.zones, 0)} gap zone${x.zones === 1 ? "" : "s"}${x.people ? ` · ${fmt(x.people, 0)} people affected` : ""}`
    + (x.opportunities ? ` · ${fmt(x.opportunities, 0)} opportunit${x.opportunities === 1 ? "y" : "ies"}` : "");
  return [
    { pick: "uk", label: "Whole UK (top 40 gap zones)", meta: count(SCOPES.uk) },
    { pick: "view", label: "What's on the map now", meta: "Follows the map as you pan and zoom" },
    ...SCOPES.nations.map((n) => ({ pick: n.scope, label: n.name, meta: count(n), group: "Nations" })),
    ...SCOPES.councils.map((c) => ({ pick: c.scope, label: c.name, meta: count(c), group: `Councils · ${c.nation}`, bbox: c.bbox })),
  ];
}

function renderCombo(filter = "") {
  const list = $("area-list");
  const f = filter.trim().toLowerCase();
  const opts = areaOptions().filter((o) => !f || o.label.toLowerCase().includes(f));
  let group = null;
  list.innerHTML = opts.slice(0, 80).map((o, i) => {
    const head = o.group && o.group !== group ? `<li class="combo-group" role="presentation">${esc(o.group)}</li>` : "";
    group = o.group || group;
    return `${head}<li role="option" id="opt-${i}" data-pick="${esc(o.pick)}" aria-selected="${o.pick === state.pick}">
      <span class="opt-label">${esc(o.label)}</span><span class="opt-meta">${esc(o.meta)}</span></li>`;
  }).join("") || `<li class="combo-empty">No matching area</li>`;
  list.querySelectorAll("[data-pick]").forEach((li) => li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(li.dataset.pick); }));
}

function openCombo(on) {
  $("area-list").hidden = !on;
  $("area-input").setAttribute("aria-expanded", String(on));
  if (on) renderCombo($("area-input").value === pickLabel() ? "" : $("area-input").value);
}

function choose(pick) {
  state.pick = pick;
  state.scope = pick === "uk" || pick === "view" ? "uk" : pick;
  $("area-input").value = pickLabel();
  openCombo(false);
  $("area-input").blur();
  closeZone();
  syncUrl();
  syncCsv();
  if (pick === "uk") map.flyTo({ ...UK_VIEW, duration: 800 });
  else if (pick.startsWith("la:")) {
    const c = SCOPES.councils.find((x) => x.scope === pick);
    if (c?.bbox) map.fitBounds([[c.bbox[0], c.bbox[1]], [c.bbox[2], c.bbox[3]]], { padding: 40, maxZoom: 11, duration: 800 });
  } else loadView();
}

async function loadScopes() {
  SCOPES = await getJSON("/api/council/scopes");
  const input = $("area-input");
  input.value = pickLabel();
  input.addEventListener("focus", () => { input.select(); openCombo(true); });
  input.addEventListener("input", () => { openCombo(true); renderCombo(input.value); });
  input.addEventListener("blur", () => setTimeout(() => { openCombo(false); input.value = pickLabel(); }, 120));
  input.addEventListener("keydown", (e) => {
    const opts = [...$("area-list").querySelectorAll("[data-pick]")];
    let i = opts.findIndex((o) => o.classList.contains("active"));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      i = e.key === "ArrowDown" ? Math.min(opts.length - 1, i + 1) : Math.max(0, i - 1);
      opts.forEach((o, j) => o.classList.toggle("active", j === i));
      opts[i]?.scrollIntoView({ block: "nearest" });
      if (opts[i]) input.setAttribute("aria-activedescendant", opts[i].id);
    } else if (e.key === "Enter" && opts.length) {
      e.preventDefault();
      choose((opts[i] || opts[0]).dataset.pick);
    } else if (e.key === "Escape") { openCombo(false); input.blur(); }
  });
}

function setShow(show) {
  state.show = show;
  document.querySelectorAll("#show-seg [data-show]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.show === show)));
  syncUrl();
  loadView();
}
document.querySelectorAll("#show-seg [data-show]").forEach((b) => b.addEventListener("click", () => setShow(b.dataset.show)));
$("opp-toggle").addEventListener("change", (e) => setShow(e.target.checked ? "both" : "gaps"));
document.querySelectorAll("#weights-seg [data-w]").forEach((b) => b.addEventListener("click", () => { state.wKind = b.dataset.w; renderSliders(); }));

/* ---------- right panel: zone (tabs: Result / How the agents decided / Evidence) or priority summary ---------- */

const mainReason = (z) => Object.entries(z.people_by_reason).sort((a, b) => b[1] - a[1])[0];

function candidateCardsHTML() {
  const d = state.detail;
  if (!d || !d.candidates.length) return "";
  const pick = agentPickIndex(), sel = selectedCandidate();
  return `<h3 class="rp-h">Candidate sites</h3><div class="cand-cards">${d.candidates.map((c, i) => `
    <button type="button" class="cand-card ${sel && sel.index === i && state.fix.mode === "with" ? "active" : ""}" data-cand="${i}">
      <span class="cand-top"><span class="cand-n">${i + 1}</span>${i === pick ? `<span class="badge">★ Recommended</span>` : ""}</span>
      <span class="cand-big">${fmt(c.people_reached, 0)} <small>people reached</small></span>
      <span class="muted">${fmt(c.zone_people_gaining, 0)} in this zone · nearest existing site ${c.nearest_existing_km == null ? "–" : `${fmt(c.nearest_existing_km, 1)} km`} · ground ${fmt(c.ground_m, 0)} m</span>
    </button>`).join("")}</div>
    <p class="muted">Select a card to see that candidate on the map: green cells gain a clear line, hatched cells stay blocked.</p>`;
}

function zoneFiguresHTML(d) {
  const z = d.zone, f = d.fix, opp = d.zone_type === "opportunity";
  const bd = (d.ranks[state.scope] || d.ranks.uk || {}).breakdown || z.breakdown || {};
  const F = opp ? OPP_FACTORS : GAP_FACTORS;
  return `
    ${opp ? `<p class="reason-tags">${z.reasons.map((r) => `<span class="cause-tag">${REASON_TAGS[r]}</span>`).join("")}${z.limited_data ? `<span class="cause-tag limited">limited data</span>` : ""}</p>` : ""}
    <ul class="plain-list">${d.summary.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
    <h4 class="rp-h4">${opp ? "Opportunity" : "Priority"} score breakdown</h4>${bar(bd, opp)}
    <dl class="kv">${Object.entries(bd).map(([k, b]) => `<dt><span class="f-dot" style="background:${F[k]?.color}"></span>${F[k]?.label || k}</dt>
      <dd>${fmt(b.contribution, 1)}${b.no_data ? " (no data: neutral)" : ""}</dd>`).join("")}</dl>
    <h4 class="rp-h4">Rule-based ${opp ? "improvement" : "fix"}</h4>
    <p><strong>${esc(f.label)}</strong>${f.secondary_label ? `, then ${esc(f.secondary_label.toLowerCase())}` : ""}</p>
    <ul class="plain-list">${f.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
    <details class="more-block"><summary>Method and limits</summary><ul class="plain-list muted">${d.limits.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>
    <p class="zone-links"><a href="${z.links.explore}">See its cells in Explore</a><a href="${z.links.signal}">My signal at its centre</a></p>`;
}

function fixCardHTML(run) {
  const f = run.result?.fix_plan, d = state.detail;
  if (!f || !d) return candidateCardsHTML();
  const opp = d.zone_type === "opportunity";
  const v = f.candidate_values || {};
  const n = f.candidate ? Number(String(f.candidate).replace("cand_", "")) : null;
  const cand = n ? d.candidates[n - 1] : null;
  const where = n ? `Candidate ${n}: high ground at ${fmt(v.ground_m, 0)} m${cand ? (cand.in_zone ? " inside the zone" : " next to the zone") : ""}, starred on the map`
    : "No single site chosen";
  const tests = d.fix.why.find((w) => /test/i.test(w));
  const NEXT = { new_site: "Check the site on the ground (planning, land, power) before approaching the operators.",
    add_operators: "Ask the operators without a site nearby about sharing the existing site.",
    capacity_upgrade: "Share the measured weakness with the operators and ask about capacity plans.",
    gather_data: "Collect measurements (drive tests or operator data) before deciding." };
  const next = d.fix.secondary_label ? `${d.fix.secondary_label}${tests ? `: ${tests.charAt(0).toLowerCase()}${tests.slice(1)}` : "."}`
    : NEXT[f.fix_type] || NEXT.new_site;
  const review = f.review ? `The Critic ${f.review.verdict === "approve" ? "approved" : "did not approve"} this plan${f.revisions ? ` after ${f.revisions} revision${f.revisions > 1 ? "s" : ""}` : ""}.` : "";
  return `<div class="rp-card fix-card">
    <p class="rp-label">${opp ? "Suggested improvement (an opportunity, not a confirmed gap)" : "Recommended fix"}</p>
    <p class="fix-title">${esc(FIX_LABELS[f.fix_type] || f.fix_type)}</p>
    <dl class="fix-dl">
      <dt>Where</dt><dd>${esc(where)}</dd>
      ${v.zone_people_gaining !== undefined ? `<dt>People reached</dt><dd><strong>${fmt(v.zone_people_gaining, 0)} of ${fmt(d.zone.people, 0)}</strong> people in the zone
        would gain a clear line of sight (${fmt(v.people_reached, 0)} people reached in all)</dd>` : ""}
      ${opp ? `<dt>Weakness</dt><dd>${esc(d.zone.reasons.map((r) => REASON_TAGS[r]).join(", "))}</dd>` : ""}
      <dt>Assumptions</dt><dd>${esc((f.assumptions || []).map(AG.plain).join("; "))}</dd>
      <dt>Suggested next step</dt><dd>${esc(next)}</dd>
    </dl>
    ${review ? `<p class="muted">${esc(review)}</p>` : ""}</div>${candidateCardsHTML()}`;
}

function zoneAfterWhy() {
  return `<div class="rp-actions"><button type="button" class="secondary" id="zone-diagnose">Diagnose again</button></div>
    <details class="more-block"><summary>Zone figures</summary>${state.detail ? zoneFiguresHTML(state.detail) : ""}</details>`;
}

function zoneIdle() {
  if (!state.detail) return `<p class="muted">Loading the zone and checking candidate sites…</p>`;
  const opp = state.detail.zone_type === "opportunity";
  return `<div class="rp-card cta"><p class="rp-label">Ask the agents</p>
      <p>They diagnose ${opp ? "the weakness behind this improvement opportunity" : "the zone's main cause"} in rounds (the Critic can ask for
      more checks), then the Fix planner proposes ${opp ? "an upgrade" : "a fix"} and the Critic reviews it. About two minutes.</p>
      <button type="button" class="primary" id="zone-diagnose">Diagnose ${opp ? "opportunity" : "zone"} (agents)</button></div>
    ${candidateCardsHTML()}
    <h3 class="rp-h">Zone figures</h3>${zoneFiguresHTML(state.detail)}`;
}

function zoneEvidenceExtra(run) {
  const d = state.detail;
  if (!d) return "";
  return `${run ? "" : `<h3 class="rp-h">Zone figures (computed)</h3><ul class="plain-list">${d.summary.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`}
    <details class="ev-sql"><summary>Data details: ${d.zetaris_queries.length} Zetaris queries · ${d.duration_ms} ms (planning ${d.planning_ms} ms)</summary>
    ${d.zetaris_queries.map((q) => `<p class="muted">${q.rows} rows · ${q.duration_ms} ms</p><pre class="sql">${esc(q.sql)}</pre>`).join("")}</details>`;
}

const view = AG.runView($("rp-tabs"), $("panel-body"), {
  idle: zoneIdle, afterHeadline: fixCardHTML, afterWhy: zoneAfterWhy, evidenceExtra: zoneEvidenceExtra,
  onRender: () => {
    const b = $("zone-diagnose");
    if (b) { b.addEventListener("click", diagnoseZone); b.disabled = AG.running(state.run); }
    $("panel-body").querySelectorAll("[data-cand]").forEach((c) => c.addEventListener("click", () => selectCandidate(Number(c.dataset.cand))));
  },
});

function setHeader(title, sub, tabs = true) {
  $("rp-title").textContent = title;
  $("rp-sub").innerHTML = sub;
  $("rp-tabs").hidden = !tabs;
}

function renderZoneRun(run) {
  state.run = run;
  view.setRun(run);
  if (!AG.running(run)) { render(); renderLegend(); }
}

async function attachZoneRun(runId) {
  state.runId = runId;
  syncUrl();
  try {
    await AG.poll(runId, renderZoneRun, () => state.runId === runId);
  } catch (e) {
    $("panel-body").innerHTML = `<p class="error">Could not follow the run: ${esc(e.message)}</p>`;
  }
}

async function startRun(zoneId) {
  return AG.post(`/api/council/zones/${zoneId}/diagnose`, { zone_type: isOpp(zoneId) ? "opportunity" : "gap" });
}

async function diagnoseZone() {
  const zoneId = state.zone;
  if ($("zone-diagnose")) $("zone-diagnose").disabled = true;
  try {
    const r = await startRun(zoneId);
    if (state.zone === zoneId) await attachZoneRun(r.run_id);
  } catch (e) {
    $("panel-body").insertAdjacentHTML("afterbegin", `<p class="error">Could not start the agents: ${esc(e.message)}</p>`);
    if ($("zone-diagnose")) $("zone-diagnose").disabled = false;
  }
}

/* ---------- "Diagnose the top 3 in view": one at a time, with the progress strip ---------- */

async function diagnoseTop3() {
  const zones = listedZones().slice(0, 3);
  if (!zones.length || state.queue) return;
  state.queue = zones.map((z) => ({ zone: z, status: "waiting", run: null, runId: null }));
  $("top3-btn").disabled = true;
  renderQueue();
  for (const item of state.queue) {
    item.status = "running";
    renderQueue();
    try {
      const r = await startRun(item.zone.zone_id);
      item.runId = r.run_id;
      item.run = await AG.poll(r.run_id, (run) => { item.run = run; renderQueue(); });
      item.status = item.run?.status || "complete";
    } catch (e) {
      item.status = "failed";
      item.error = e.message;
    }
    renderQueue();
  }
  const done = state.queue;
  state.queue = null;
  $("top3-btn").disabled = false;
  renderQueue(done);
}

function renderQueue(finished = null) {
  const q = state.queue || finished;
  const el = $("queue");
  el.hidden = !q;
  if (!q) return;
  el.innerHTML = `<p class="queue-head"><strong>Diagnosing the top ${q.length} in view</strong>, one at a time (about two minutes each)
    ${finished ? `<button type="button" class="link-btn" id="queue-close">Close</button>` : ""}</p>
    <ol>${q.map((it) => `<li><button type="button" class="link" data-open="${it.zone.zone_id}" data-run="${it.runId || ""}">${esc(it.zone.name)}</button>
      <span class="muted">${it.zone.zone_type === "opportunity" ? "opportunity" : "gap zone"} · ${esc(it.status)}${it.run?.result?.headline && !AG.running(it.run) ? `: ${esc(AG.plain(it.run.result.headline))}` : ""}</span>
      ${it.status === "running" && it.run ? AG.progressHTML(it.run) : ""}</li>`).join("")}</ol>`;
  el.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openZone(b.dataset.open, b.dataset.run || null)));
  if ($("queue-close")) $("queue-close").addEventListener("click", () => { $("queue").hidden = true; });
}
$("top3-btn").addEventListener("click", diagnoseTop3);

/* ---------- priority summary of what is listed ---------- */

function summarySection(kind, sec) {
  const lines = String(sec.body_markdown || "").split("\n");
  const prose = AG.markdown(lines.filter((l) => !/\[[ZO]\d{5}\]/.test(l)).join("\n"));
  const items = sec.zone_rows.map((z) => {
    const opp = kind === "opportunity";
    const tags = opp ? (z.reasons || []).map((r) => REASON_TAGS[r]) : [REASON_TAGS[mainReason(z)[0]]];
    const n = mainReason(z)[1];
    return `<li class="prio-item"><button type="button" class="prio-open" data-zone="${z.zone_id}">
      <span class="prio-top"><strong>${esc(z.name)}</strong>${tags.map((t) => `<span class="cause-tag">${esc(t)}</span>`).join("")}</span>
      <span class="prio-people">${fmt(n, 0)} of ${fmt(z.people, 0)} people</span>
      <span class="muted prio-meta">${esc(z.zone_id)} · ${esc(z.council)} · ${fmt(z.tests, 0)} test${z.tests === 1 ? "" : "s"}${opp && z.limited_data ? " · limited data" : ""}${z.diagnosis ? ` · agents: ${esc(AG.plain(z.diagnosis))}` : ""}</span>
    </button></li>`;
  }).join("");
  return `<h3 class="rp-h">${kind === "gap" ? "Gap zones" : "Improvement opportunities"}</h3>${prose}<ol class="prio">${items}</ol>`;
}

async function prioritySummary() {
  closeZone();
  const zones = listedZones();
  const gapIds = zones.filter((z) => z.zone_type === "gap").map((z) => z.zone_id).slice(0, 40);
  const oppIds = zones.filter((z) => z.zone_type === "opportunity").map((z) => z.zone_id).slice(0, 60);
  const label = state.pick === "view" ? "the current map view" : state.pick === "uk" ? "the UK (top 40 gap zones)" : scopeName(state.pick);
  state.panel = "summary";
  $("panel").hidden = false;
  setCollapsed(false);
  setHeader("Priority summary", `${esc(label)} · ${gapIds.length} gap zones, ${oppIds.length} opportunities listed`, false);
  $("panel-body").innerHTML = `<p class="muted">The Advisor is summarising what is listed, using any agent diagnoses in Meterless…</p>`;
  try {
    const r = await AG.post("/api/council/summary", { scope: state.scope, label, weights: weightParam(), opp_weights: oppWeightParam(),
      gap_ids: gapIds, opportunity_ids: oppIds });
    if (state.panel !== "summary") return;
    const secs = Object.entries(r.sections);
    setHeader("Priority summary", `${esc(label)} · ${secs.map(([k, s]) => `${s.zones.length} ${k === "gap" ? "gap zones" : "opportunities"}`).join(", ")}`
      + (r.guard.length ? ` · ${r.guard.length} guard correction${r.guard.length > 1 ? "s" : ""}` : ""), false);
    $("panel-body").innerHTML = `<div class="summary-panel">${secs.map(([k, s]) => summarySection(k, s)).join("")}
      <div class="rp-actions"><button type="button" class="secondary" id="summary-download">Download .md</button></div>
      <p class="muted rp-foot">Written by the Advisor agent from the listed zones; numbers are checked against the zone data by code.
      The lists are built from that data. Scores rank need, not certainty.</p></div>`;
    const md = `# ${r.title}\n\n${r.body_markdown}\n`;
    $("summary-download").addEventListener("click", () => AG.download(`priority-summary-${state.scope.replace(/\W+/g, "-")}.md`, md));
    $("panel-body").querySelectorAll(".prio-open").forEach((b) => b.addEventListener("click", () => openZone(b.dataset.zone)));
  } catch (e) {
    $("panel-body").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}
$("summary-btn").addEventListener("click", prioritySummary);

/* ---------- open / close a zone; fit and reset view ---------- */

function fitZone(withCandidate = false) {
  const d = state.detail;
  if (!d) return;
  const cand = withCandidate ? selectedCandidate() : null;
  const pts = [...d.cells.map((c) => [c.lon, c.lat]), ...(cand ? [[cand.lon, cand.lat]] : d.candidates.map((c) => [c.lon, c.lat]))];
  const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
  const wide = window.innerWidth > 760;
  map.fitBounds([[Math.min(...lons) - 0.02, Math.min(...lats) - 0.015], [Math.max(...lons) + 0.02, Math.max(...lats) + 0.015]],
    { padding: { top: 110, bottom: 150, left: 150, right: wide && !$("panel").classList.contains("collapsed") ? 500 : 50 }, maxZoom: 12, duration: 900 });
}

async function openZone(zoneId, runId = null, diagnose = false) {
  if (!state.zone && !state.prevView) state.prevView = { center: map.getCenter(), zoom: map.getZoom() };
  state.zone = zoneId;
  state.panel = "zone";
  state.run = state.runId = null;
  state.detail = null;
  state.fix = { cand: null, chosen: false, mode: "today", showAll: false, hover: null };
  syncUrl();
  render();
  document.querySelectorAll("tr[data-zone]").forEach((tr) => tr.classList.toggle("selected", tr.dataset.zone === zoneId));
  const row = listedZones().find((z) => z.zone_id === zoneId);
  const kindWord = isOpp(zoneId) ? "Improvement opportunity" : "Gap zone";
  setHeader(row ? row.name : zoneId, `${kindWord} · ${row ? `${esc(row.council)} · ` : ""}${zoneId}`);
  $("panel").hidden = false;
  setCollapsed(false);
  view.reset();
  try {
    const d = await getJSON(`/api/council/zones/${zoneId}?weights=${encodeURIComponent(isOpp(zoneId) ? oppWeightParam() : weightParam())}`);
    if (state.zone !== zoneId) return;
    state.detail = d;
    const z = d.zone;
    const rk = d.ranks[state.scope] || d.ranks.uk;
    setHeader(z.name, `${kindWord} · ${z.zone_id} · ${esc(z.council)}${rk ? ` · #${rk.rank} of ${rk.of} in ${esc(scopeName(state.scope in d.ranks ? state.scope : "uk"))}` : ""}`);
    view.render();
    $("panel-body").scrollTop = 0;
    $("reset-view").hidden = !state.prevView;
    fitZone();
    renderLegend();
    render();
    if (runId) attachZoneRun(runId);
    else if (diagnose) diagnoseZone();
  } catch (e) {
    $("panel-body").innerHTML = `<p class="error">Could not load the zone: ${esc(e.message)}</p>`;
  }
}

function closeZone() {
  state.zone = null;
  state.panel = null;
  state.detail = null;
  state.run = state.runId = null;
  $("panel").hidden = true;
  $("fix-switch").hidden = true;
  document.querySelectorAll("tr.selected").forEach((tr) => tr.classList.remove("selected"));
  syncUrl();
  renderLegend();
  render();
}

function resetView() {
  if (state.prevView) map.flyTo({ ...state.prevView, duration: 900 });
  state.prevView = null;
  $("reset-view").hidden = true;
}

function setCollapsed(on) {
  $("panel").classList.toggle("collapsed", on);
  $("panel-collapse").setAttribute("aria-expanded", String(!on));
  $("panel-collapse").setAttribute("aria-label", on ? "Show panel" : "Hide panel");
  $("panel-collapse").textContent = on ? "‹" : "›";
}

$("panel-close").addEventListener("click", closeZone);
$("panel-collapse").addEventListener("click", () => setCollapsed(!$("panel").classList.contains("collapsed")));
$("reset-view").addEventListener("click", resetView);

function syncUrl() {
  const p = new URLSearchParams();
  if (state.pick !== "view") p.set("area", state.pick);
  if (state.show !== "both") p.set("show", state.show);
  if (state.zone) p.set("zone", state.zone);
  if (state.zone && state.runId) p.set("run", state.runId);
  history.replaceState(null, "", `/council${p.toString() ? `?${p}` : ""}`);
}

$("reset").addEventListener("click", () => {
  if (state.wKind === "opportunity") state.oppWeights = { ...DEFAULT_OPP_WEIGHTS };
  else state.weights = { ...DEFAULT_WEIGHTS };
  renderSliders();
  loadView();
});

/* ---------- start ---------- */

map.on("load", async () => {
  const params = new URLSearchParams(location.search);
  const legacyScope = params.get("scope");
  state.pick = params.get("area") || (legacyScope && legacyScope !== "uk" ? legacyScope : "view");
  state.scope = state.pick === "uk" || state.pick === "view" ? "uk" : state.pick;
  state.show = params.get("show") || "both";
  document.querySelectorAll("#show-seg [data-show]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.show === state.show)));
  renderSliders();
  renderLegend();
  try {
    await loadScopes();
    const c = SCOPES.councils.find((x) => x.scope === state.pick);
    if (params.get("lat") && params.get("lon")) map.jumpTo({ center: [+params.get("lon"), +params.get("lat")], zoom: +(params.get("zoom") || 10) });
    else if (c?.bbox && !params.get("zone")) map.fitBounds([[c.bbox[0], c.bbox[1]], [c.bbox[2], c.bbox[3]]], { padding: 40, maxZoom: 11, duration: 0 });
    await loadView();
    if (params.get("zone")) openZone(params.get("zone"), params.get("run"));
  } catch (e) {
    $("list-summary").innerHTML = `<span class="error">${esc(e.message)}</span>`;
  }
});
