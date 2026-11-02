/* Coverage Detective data explorer: MapLibre basemap + deck.gl H3 hexagons. */
"use strict";

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const UK_VIEW = { center: [-3.2, 54.6], zoom: 4.9 };
const MAST_MIN_ZOOM = 10;
const TILE_MIN_ZOOM = 12;
const NO_DATA = [168, 174, 184, 70];
const SOURCE_COLORS = { opencellid: "#1D4ED8", osm: "#C2410C", both: "#0F766E", wtr: "#7C3AED" };
const sourceColor = (sources) =>
  sources.includes("ofcom_wtr") ? SOURCE_COLORS.wtr
  : sources === "opencellid,osm" ? SOURCE_COLORS.both
  : SOURCE_COLORS[sources] || "#5B6472";
const SPEED = { breaks: [2, 5, 10], colors: ["#C2410C", "#F59E0B", "#93C5FD", "#2563EB"] };
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

const state = {
  layers: [],          // layer metadata from /api/layers
  layer: null,         // selected layer metadata
  values: new Map(),   // cell_id -> [value, weak]
  grid: [],            // every cell id (from the population layer)
  inGrid: new Set(),
  masts: [],
  tiles: [],
  selected: null,
  zone: null,          // gap zone outlined from the Council view (?zone=)
  point: null,         // where the cell was clicked or searched (for "Check an exact location here")
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: 0 }));

function hexToRgb(hex, alpha = 255) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, alpha];
}

function colorFor(scale, value) {
  if (scale.type === "categories") {
    const i = scale.values.indexOf(value);
    return i < 0 ? null : scale.colors[i];
  }
  let i = 0;
  while (i < scale.breaks.length && value >= scale.breaks[i]) i++;
  return scale.colors[i];
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

/* ---------- map ---------- */

const map = new maplibregl.Map({ container: "map", style: BASEMAP, ...UK_VIEW, attributionControl: { compact: true } });
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
const overlay = new deck.MapboxOverlay({ interleaved: false, layers: [], getCursor: ({ isHovering }) => (isHovering ? "pointer" : "grab") });
map.addControl(overlay);
map.getCanvas().setAttribute("aria-label", "Map of UK mobile coverage cells");

function hexLayer() {
  const scale = state.layer?.scale;
  const fill = (id) => {
    const entry = state.values.get(id);
    if (!entry || !scale) return NO_DATA;
    const c = colorFor(scale, entry[0]);
    if (!c) return NO_DATA;
    return hexToRgb(c, entry[1] ? 80 : 205);
  };
  return new deck.H3HexagonLayer({
    id: "cells",
    data: state.grid,
    getHexagon: (d) => d,
    getFillColor: fill,
    stroked: false,
    extruded: false,
    filled: true,
    pickable: true,
    coverage: 0.94,
    updateTriggers: { getFillColor: [state.layer?.name, state.values.size] },
  });
}

function selectedLayer() {
  // Outline drawn as a polygon: H3HexagonLayer does not reliably stroke at this resolution.
  const ring = state.selected ? h3.cellToBoundary(state.selected, true) : null;
  return new deck.PolygonLayer({
    id: "selected",
    data: ring ? [ring] : [],
    getPolygon: (d) => d,
    filled: true,
    getFillColor: [21, 25, 31, 18],
    stroked: true,
    getLineColor: [21, 25, 31, 255],
    lineWidthUnits: "pixels",
    getLineWidth: 3,
  });
}

function zoneLayer() {
  if (!state.zone) return null;
  return new deck.PolygonLayer({
    id: "zone", data: state.zone.cell_ids.map((c) => h3.cellToBoundary(c, true)), getPolygon: (p) => p,
    filled: false, stroked: true, getLineColor: [194, 65, 12, 255], lineWidthUnits: "pixels", getLineWidth: 3,
  });
}

function tileLayer() {
  return new deck.PolygonLayer({
    id: "tiles",
    data: map.getZoom() >= TILE_MIN_ZOOM ? state.tiles : [],
    getPolygon: (t) => [[t.min_lon, t.min_lat], [t.max_lon, t.min_lat], [t.max_lon, t.max_lat], [t.min_lon, t.max_lat]],
    getFillColor: (t) => hexToRgb(colorFor({ type: "bins", ...SPEED }, t.download_mbps), t.tests < 10 ? 110 : 230),
    getLineColor: [255, 255, 255, 200],
    lineWidthUnits: "pixels",
    getLineWidth: 1,
    pickable: true,
  });
}

function mastLayer() {
  return new deck.ScatterplotLayer({
    id: "masts",
    data: map.getZoom() >= MAST_MIN_ZOOM ? state.masts : [],
    getPosition: (m) => [m.lon, m.lat],
    getFillColor: (m) => hexToRgb(sourceColor(m.sources)),
    getLineColor: [255, 255, 255, 255],
    stroked: true,
    lineWidthUnits: "pixels",
    getLineWidth: 1.5,
    radiusUnits: "pixels",
    getRadius: 5,
    pickable: true,
  });
}

function render() {
  overlay.setProps({
    layers: [hexLayer(), tileLayer(), zoneLayer(), selectedLayer(), mastLayer()].filter(Boolean),
    onClick: onMapClick,
    onHover: onHover,
  });
  const z = map.getZoom();
  $("zoom-hint").textContent =
    z < MAST_MIN_ZOOM ? `Zoom in to see approximate mast sites (zoom ${MAST_MIN_ZOOM}+)` :
    z < TILE_MIN_ZOOM ? `Zoom in further for Ookla speed tiles (zoom ${TILE_MIN_ZOOM}+)` :
    `Showing Ookla tiles (${state.tilesQuarter || "latest quarter"}) and mast sites`;
  if (state.zone) $("zoom-hint").textContent = `Gap zone ${state.zone.zone_id}, ${state.zone.name}, outlined in orange · ` + $("zoom-hint").textContent;
}

/* ---------- layers ---------- */

async function loadLayer(name) {
  state.layer = state.layers.find((l) => l.name === name);
  const rows = await getJSON(`/api/layer/${name}`);
  state.values = new Map(rows.map((r) => [r[0], [r[1], r[2] === 1]]));
  renderLegend();
  render();
}

function renderLegend() {
  const l = state.layer;
  const s = l.scale;
  const items = s.colors.map((c, i) => `<li><span class="swatch" style="background:${c}"></span>${esc(s.labels[i])}</li>`).join("");
  const weakNote = ["download_mbps", "trend_pct"].includes(l.name) ? `<p class="note">Faded cells: weak evidence (fewer than 10 tests).</p>` : "";
  const modelNote = l.model_estimate ? `<p class="model-note">Model estimate, not a measurement. Terrain only (no buildings), crowd-sourced mast sites, ` +
    `assumed antennas; only the regions modelled so far. Check an address on <a href="https://www.ofcom.org.uk/mobile-coverage-checker" target="_blank" rel="noopener">Ofcom's coverage checker</a>.</p>` : "";
  $("legend").innerHTML = `
    <h2>${esc(l.title)}</h2>
    <p class="units">${esc([l.units, l.data_period].filter(Boolean).join(" · "))}</p>
    ${modelNote}
    <ul>${items}<li><span class="swatch" style="background:rgba(168,174,184,0.35)"></span>${l.model_estimate ? "Not modelled" : "No data"}</li></ul>
    ${weakNote}
    <ul class="masts">
      <li><span class="dot" style="background:${SOURCE_COLORS.opencellid}"></span>Mast site · OpenCelliD</li>
      <li><span class="dot" style="background:${SOURCE_COLORS.osm}"></span>Mast site · OpenStreetMap</li>
      <li><span class="dot" style="background:${SOURCE_COLORS.both}"></span>Mast site · OpenCelliD + OSM</li>
      <li><span class="dot" style="background:${SOURCE_COLORS.wtr}"></span>Mast site · Ofcom licence register</li>
    </ul>`;
}

function renderDataDates() {
  const by = Object.fromEntries(state.layers.map((l) => [l.name, l]));
  const parts = [];
  if (by.download_mbps) parts.push(`Speeds ${by.download_mbps.data_period}`);
  if (by.nearest_mast_km) parts.push(`Masts ${by.nearest_mast_km.as_of}`);
  if (by.ofcom_geo_4g_none_pct) parts.push(`Ofcom ${by.ofcom_geo_4g_none_pct.data_period.slice(0, 7)}`);
  $("data-dates").textContent = parts.join(" · ");
}

/* ---------- masts and tiles for the visible area ---------- */

let viewTimer = null;
function onViewChange() {
  clearTimeout(viewTimer);
  viewTimer = setTimeout(async () => {
    const z = map.getZoom();
    const b = map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(",");
    try {
      if (z >= MAST_MIN_ZOOM) state.masts = (await getJSON(`/api/masts?bbox=${bbox}`)).sites;
      if (z >= TILE_MIN_ZOOM) {
        const t = await getJSON(`/api/tiles?bbox=${bbox}`);
        state.tiles = t.tiles;
        state.tilesQuarter = t.quarter;
      }
    } catch (e) {
      console.error(e);
    }
    render();
  }, 250);
  render();
}

/* ---------- hover and click ---------- */

function onHover(info) {
  const tip = $("tooltip");
  if (!info.object) { tip.hidden = true; return; }
  let html = "";
  if (info.layer.id === "cells") {
    const entry = state.values.get(info.object);
    const v = entry ? entry[0] : null;
    html = `<strong>${esc(state.layer.title)}:</strong> ${v === null ? "no data" : esc(typeof v === "number" ? fmt(v, 2) : v)} ${v === null ? "" : esc(state.layer.units || "")}` +
      (entry && entry[1] ? `<br><span>weak evidence</span>` : "") +
      (state.layer.model_estimate && entry ? `<br><span>model estimate, not a measurement</span>` : "");
  } else if (info.layer.id === "masts") {
    const m = info.object;
    html = `<strong>Approximate mast site</strong><br>Sources: ${esc(m.sources)}` +
      (m.operators_confirmed ? `<br>Confirmed (Ofcom register): ${esc(m.operators_confirmed)}` : "") +
      (m.operators_inferred ? `<br>Inferred: ${esc(m.operators_inferred)}` : "") +
      (!m.operators ? "<br>Operators: unknown" : "") +
      (m.has_microwave_backhaul ? `<br>Microwave backhaul · antenna ${fmt(m.antenna_height_m, 0)} m` : "") +
      (m.radio_types ? `<br>Radio: ${esc(m.radio_types)}` : "") + (m.osm_operator ? `<br>OSM operator tag: ${esc(m.osm_operator)}` : "");
  } else if (info.layer.id === "tiles") {
    const t = info.object;
    html = `<strong>Ookla tile</strong> (~350–400 m)<br>${fmt(t.download_mbps, 1)} Mbps down · ${fmt(t.upload_mbps, 1)} up<br>${t.tests} tests · ${t.devices} devices`;
  }
  tip.innerHTML = html;
  tip.style.left = `${info.x}px`;
  tip.style.top = `${info.y}px`;
  tip.hidden = !html;
}

function onMapClick(info) {
  if (!info.coordinate) return;
  const [lon, lat] = info.coordinate;
  const cell = h3.latLngToCell(lat, lon, 7);
  if (state.inGrid.has(cell)) selectCell(cell, { lat, lon });  // ignore clicks on the sea or abroad
}

/* ---------- side panel ---------- */

function compass(deg) {
  return COMPASS[Math.round(deg / 22.5) % 16];
}

function statusSentence(c) {
  const n = c.masts_within_5km, u = c.masts_unknown_operator_within_5km;
  const unknownNote = u ? ` Operators could not be identified for ${u} nearby site${u === 1 ? "" : "s"}, so more may be present.` : "";
  switch (c.operator_data_status) {
    case "confirmed": return `At least one operator nearby is confirmed by Ofcom's licence register; the others are inferred from crowd-sourced data.${unknownNote}`;
    case "inferred": return `Operators are inferred from crowd-sourced data (OpenCelliD, OpenStreetMap); none confirmed by Ofcom's licence register.${unknownNote}`;
    case "unknown": return `Operators could not be identified for any of the ${n} nearby sites.`;
    case "no_sites_found": return "No approximate mast sites found within 5 km. This is weak evidence: OpenCelliD's free data is incomplete.";
    default: return "";
  }
}

function sparkline(history) {
  if (!history?.length) return "";
  const max = Math.max(...history.map((h) => h.download_mbps), 1);
  return `<div class="spark" aria-label="Download speed by quarter">${history.map((h) =>
    `<div class="bar" title="${esc(h.quarter)}: ${fmt(h.download_mbps, 1)} Mbps, ${h.tests} tests">
       <span>${fmt(h.download_mbps, 0)}</span>
       <div class="fill ${h.tests < 10 ? "weak-bar" : ""}" style="height:${Math.max(3, (h.download_mbps / max) * 36)}px"></div>
       <span>${esc(h.quarter.replace("20", "'"))}</span></div>`).join("")}</div>`;
}

function panelHTML(res) {
  const c = res.cell;
  const ops = ["EE", "O2", "Three", "Vodafone"];
  const near = (c.operators_within_5km || "").split(",").filter(Boolean);
  const confirmed = (c.operators_confirmed_within_5km || "").split(",").filter(Boolean);
  const chip = (o) => !near.includes(o) ? `<span class="chip off">${o}</span>`
    : confirmed.includes(o) ? `<span class="chip confirmed" title="Confirmed by Ofcom's licence register">✓ ${o}</span>`
    : `<span class="chip" title="Inferred from OpenCelliD / OpenStreetMap">${o}</span>`;
  const speed = c.tests
    ? `<div class="big">${fmt(c.download_mbps, 1)}<small>Mbps down</small></div>
       <dl class="kv"><dt>Upload</dt><dd>${fmt(c.upload_mbps, 1)} Mbps</dd><dt>Latency</dt><dd>${fmt(c.latency_ms, 0)} ms</dd>
       <dt>Tests · devices</dt><dd>${c.tests} · ${c.devices}</dd><dt>Quarter</dt><dd>${esc(c.quarter)}</dd></dl>`
    : `<p class="muted">No Ookla tests in this cell in the latest quarter.</p>`;
  const weak = c.tests && c.tests < 10 ? `<p class="weak">Weak evidence: only ${c.tests} test${c.tests === 1 ? "" : "s"} in this cell.</p>` : "";
  const nb = c.neighbour_tests
    ? `<p class="status">Neighbouring cells (${c.neighbour_cells_with_data} of 6 with data): <strong>${fmt(c.neighbour_download_mbps, 1)} Mbps</strong> from ${c.neighbour_tests} tests${c.neighbour_tests < 10 ? " (weak)" : ""}.</p>`
    : `<p class="status muted">No tests in the neighbouring cells either.</p>`;
  const trend = c.quarters_with_data >= 2
    ? `<p class="status">${c.download_change_pct >= 0 ? "Up" : "Down"} <strong>${fmt(Math.abs(c.download_change_pct), 0)}%</strong> from ${fmt(c.download_mbps_oldest, 1)} Mbps over ${c.quarters_with_data} quarters.</p>`
    : `<p class="status muted">Not enough quarters for a trend.</p>`;
  const mast = c.nearest_mast_km !== null && c.nearest_mast_km !== undefined
    ? `<dl class="kv"><dt>Nearest site</dt><dd>${fmt(c.nearest_mast_km, 2)} km ${compass(c.nearest_mast_bearing_deg)} (${fmt(c.nearest_mast_bearing_deg, 0)}°)</dd>
       <dt>Operators there</dt><dd>${esc(c.nearest_mast_operators || "unknown")}</dd>
       ${c.nearest_mast_operators_confirmed ? `<dt>Confirmed by Ofcom's licence register</dt><dd>${esc(c.nearest_mast_operators_confirmed)}</dd>` : ""}
       ${c.nearest_mast_antenna_height_m ? `<dt>Antenna height (register)</dt><dd>${fmt(c.nearest_mast_antenna_height_m, 0)} m</dd>` : ""}
       <dt>Sources</dt><dd>${esc(c.nearest_mast_sources)}</dd></dl>`
    : "";
  const ofcom = c.pcon_name
    ? `<p class="status">In <strong>${esc(c.pcon_name)}</strong>, ${fmt(c.g4_geo_out_0, 1)}% of land has no outdoor 4G from any operator and ${fmt(c.g4_geo_out_1, 1)}% from only one.</p>
       <dl class="kv"><dt>Premises with no indoor 4G</dt><dd>${fmt(c.g4_prem_in_0, 1)}%</dd><dt>A/B roads with no in-car 4G</dt><dd>${fmt(c.g4_abrd_in_0, 1)}%</dd>
       <dt>Premises with 5G outdoors</dt><dd>${fmt(c.g5hc_prem_out_at_least_one, 1)}%</dd><dt>${esc(c.laua_name)} (land, no 4G)</dt><dd>${fmt(c.laua_g4_geo_out_0, 1)}%</dd></dl>
       <p class="footnote">Ofcom Connected Nations (${esc(c.ofcom_data_date)}): predicted coverage for the whole area, not this exact location.</p>`
    : "";
  const dates = Object.entries(res.data_dates).map(([t, m]) => `<dt class="mono">${esc(t)}</dt><dd>${esc(m.data_period)}</dd>`).join("");
  return `
    <h2>${esc(c.place || c.laua_name || "Selected cell")}</h2>
    <p class="sub">${esc(c.nation)} · ${esc(c.pcon_name || "")} · <span class="mono">${esc(c.cell_id)}</span></p>
    <dl class="kv"><dt>People in this cell</dt><dd>${fmt(c.population, 0)}</dd></dl>
    <section><h3>Measured speed</h3>${speed}${weak}${nb}</section>
    <section><h3>Trend</h3>${trend}${sparkline(c.history)}</section>
    <section><h3>Nearest mast site</h3>${mast}</section>
    <section><h3>Line of sight from the cell centre</h3>${losGrade(c)}</section>
    ${c.elevation_mean_m !== undefined && c.elevation_mean_m !== null ? `<section><h3>Terrain in this cell</h3>
      <dl class="kv"><dt>Mean elevation</dt><dd>${fmt(c.elevation_mean_m, 0)} m</dd><dt>Elevation range</dt><dd>${fmt(c.elevation_range_m, 0)} m</dd></dl>
      <p class="footnote">Copernicus DEM GLO-30, a surface model (includes some trees and buildings).</p></section>` : ""}
    <section><h3>Operators within 5 km</h3>
      <div class="chips">${ops.map(chip).join("")}</div>
      ${confirmed.length ? `<p class="footnote">✓ Confirmed by Ofcom's licence register (licensed microwave links); others inferred from crowd-sourced data.</p>` : ""}
      <p class="status">${c.masts_within_5km} approximate mast sites (${c.masts_known_operator_within_5km} with a known operator).</p>
      <p class="status">${c.microwave_backhaul_sites_within_5km
        ? `${c.microwave_backhaul_sites_within_5km} of them have licensed microwave backhaul (${c.wtr_sites_within_5km} in Ofcom's register).`
        : "None of them appear in Ofcom's licence register."}</p>
      <p class="status">${esc(statusSentence(c))}</p></section>
    <section><h3>Ofcom: the wider area</h3>${ofcom}</section>
    <section><h3>Data dates</h3><dl class="kv">${dates}</dl></section>
    <details><summary>Data details: SQL query (${res.query.duration_ms} ms, DuckDB)</summary><pre class="sql">${esc(res.query.sql)}</pre></details>
    <a class="diagnose go" href="/signal?lat=${state.point.lat.toFixed(5)}&lon=${state.point.lon.toFixed(5)}">Check an exact location here</a>
    <p class="diagnose-note">Opens My signal at ${state.point.exact ? "the point you clicked" : "the cell centre"}: speeds within 1 km,
      each network's nearest site and line of sight from that exact spot.</p>`;
}

function losGrade(c) {
  if (!c.los_class) return `<p class="muted">Not computed for this cell (no people or no site).</p>`;
  const what = { clear: "Clear", marginal: "Marginal", blocked: "Blocked" }[c.los_class];
  const detail = c.los_class === "clear" ? `the line clears the ground by ${fmt(-c.los_obstruction_m, 0)} m`
    : `the ground rises ${fmt(c.los_obstruction_m, 0)} m above the line${c.los_class === "marginal" ? " (may be trees or buildings)" : ""}`;
  return `<p class="status"><strong class="${c.los_class === "clear" ? "good" : "bad"}">${what}</strong> from the cell centre to its nearest
    site: ${detail}.</p><p class="footnote">A grade for the whole cell. Blocked means weaker signal, not none. For your exact spot, use
    "Check an exact location here".</p>`;
}

async function selectCell(cellId, point = null) {
  state.selected = cellId;
  const [clat, clon] = h3.cellToLatLng(cellId);
  state.point = point ? { ...point, exact: true } : { lat: clat, lon: clon, exact: false };
  render();
  const panel = $("panel");
  panel.hidden = false;
  $("panel-body").innerHTML = `<p class="loading">Querying…</p>`;
  try {
    const res = await getJSON(`/api/cell/${cellId}`);
    if (state.selected === cellId) $("panel-body").innerHTML = panelHTML(res);
  } catch (e) {
    $("panel-body").innerHTML = `<p class="error">Could not load this cell: ${esc(e.message)}</p>`;
  }
}

$("panel-close").addEventListener("click", () => {
  $("panel").hidden = true;
  state.selected = null;
  render();
});

/* ---------- search ---------- */

$("search").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("search-input").value.trim();
  const list = $("search-results");
  if (q.length < 2) return;
  list.hidden = false;
  list.innerHTML = `<li class="empty">Searching…</li>`;
  try {
    const { results } = await getJSON(`/api/geocode?q=${encodeURIComponent(q)}`);
    if (!results.length) { list.innerHTML = `<li class="empty">No match. Try a postcode.</li>`; return; }
    list.innerHTML = results.map((r, i) => `<li tabindex="0" data-i="${i}">${esc(r.label)}${r.approximate ? " (approximate match)" : ""}<div class="detail">${esc([r.postcode ? r.admin_district : r.local_type, r.postcode ? r.country : r.admin_district].filter(Boolean).join(" · "))}</div></li>`).join("");
    list.querySelectorAll("li[data-i]").forEach((li) => {
      const go = () => { const r = results[+li.dataset.i]; list.hidden = true; flyTo(r); };
      li.addEventListener("click", go);
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
    if (results.length === 1) { list.hidden = true; flyTo(results[0]); }
  } catch (e) {
    list.innerHTML = `<li class="empty">Search failed: ${esc(e.message)}</li>`;
  }
});
document.addEventListener("click", (e) => { if (!$("search").contains(e.target)) $("search-results").hidden = true; });

function flyTo(r) {
  map.flyTo({ center: [r.lon, r.lat], zoom: 11.5, speed: 1.6 });
  selectCell(r.cell_id, { lat: r.lat, lon: r.lon, label: r.label });
}

/* ---------- start ---------- */

async function start() {
  state.layers = await getJSON("/api/layers");
  const select = $("layer-select");
  select.innerHTML = state.layers.map((l) => `<option value="${l.name}">${esc(l.title)}</option>`).join("");
  select.addEventListener("change", () => loadLayer(select.value));
  renderDataDates();
  state.grid = (await getJSON("/api/layer/population")).map((r) => r[0]);
  state.inGrid = new Set(state.grid);
  const params = new URLSearchParams(location.search);
  const first = params.get("layer") || "download_mbps";
  select.value = first;
  await loadLayer(first);
  map.on("moveend", onViewChange);
  if (params.get("lat") && params.get("lon")) {
    map.jumpTo({ center: [+params.get("lon"), +params.get("lat")], zoom: +(params.get("zoom") || 11) });
  }
  if (params.get("cell")) selectCell(params.get("cell"));
  if (params.get("zone")) {
    try {
      state.zone = await getJSON(`/api/council/zones/${encodeURIComponent(params.get("zone"))}/cells`);
      render();
    } catch (e) { /* unknown zone: show the map without it */ }
  }
  onViewChange();
}

map.on("load", () => start().catch((e) => { $("legend").innerHTML = `<p class="error">Could not start: ${esc(e.message)}</p>`; }));
