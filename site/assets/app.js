import { CONFIG } from "../config.js";
import { bandChart, barChart, fmtPrice, stripPlot, torontoDay, torontoHour } from "./charts.js";

const preview = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const logBase = preview ? CONFIG.previewLogBase : CONFIG.logBase;

// ---------------------------------------------------------------- theme toggle
function initTheme() {
  const btn = document.querySelector(".theme-toggle");
  const order = ["system", "light", "dark"];
  let mode = "system";
  try { mode = localStorage.getItem("gridcast-theme") || "system"; } catch { /* storage blocked */ }
  const apply = () => {
    if (mode === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    if (btn) btn.textContent = `Theme: ${mode}`;
  };
  apply();
  btn?.addEventListener("click", () => {
    mode = order[(order.indexOf(mode) + 1) % order.length];
    try { localStorage.setItem("gridcast-theme", mode); } catch { /* storage blocked */ }
    apply();
    location.reload(); // charts read colour tokens at draw time
  });
}

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

function text(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function table(container, headers, rows) {
  const t = document.createElement("table");
  const head = t.createTHead().insertRow();
  for (const h of headers) {
    const th = document.createElement("th");
    th.textContent = h;
    head.appendChild(th);
  }
  const body = t.createTBody();
  for (const r of rows) {
    const tr = body.insertRow();
    for (const cell of r) tr.insertCell().textContent = cell;
  }
  container.replaceChildren(t);
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ------------------------------------------------------------------ forecast page
async function forecastPage() {
  if (preview) document.getElementById("preview-flag").hidden = false;
  let latest;
  try {
    latest = await getJSON(`${logBase}latest.json`);
  } catch {
    document.getElementById("forecast-body").replaceChildren(Object.assign(document.createElement("div"), {
      className: "empty", textContent: "The first public forecast has not been published yet.",
    }));
    latest = null;
  }
  if (latest) renderForecast(latest);
  try {
    renderTrackRecord(await getJSON(`${logBase}scorecard.json`));
  } catch {
    renderTrackRecord(null);
  }
}

function renderForecast(f) {
  const lv = f.quantile_levels;
  const at = (q) => lv.findIndex((v) => Math.abs(v - q) < 1e-9);
  const idx = { q01: at(0.01), q05: at(0.05), q25: at(0.25), q50: at(0.5), q75: at(0.75), q95: at(0.95), q99: at(0.99) };
  const points = f.hours.map((h) => ({
    ts: h.ts_utc,
    he: h.hour_ending,
    median: h.quantiles[idx.q50],
    bands: { b98: [h.quantiles[idx.q01], h.quantiles[idx.q99]], b90: [h.quantiles[idx.q05], h.quantiles[idx.q95]], b50: [h.quantiles[idx.q25], h.quantiles[idx.q75]] },
  }));
  const medians = points.map((p) => p.median);
  const avg = medians.reduce((a, b) => a + b, 0) / medians.length;
  const peak = points.reduce((a, b) => (b.median > a.median ? b : a));
  const low = points.reduce((a, b) => (b.median < a.median ? b : a));
  text("fc-day", torontoDay(`${f.target_day}T12:00:00Z`));
  text("fc-avg", fmtPrice(avg));
  text("fc-peak", fmtPrice(peak.median));
  text("fc-peak-note", `${torontoHour(peak.ts, { minute: "2-digit" })} · 90% range ${fmtPrice(peak.bands.b90[0])}–${fmtPrice(peak.bands.b90[1])}`);
  text("fc-low", fmtPrice(low.median));
  text("fc-low-note", `${torontoHour(low.ts, { minute: "2-digit" })} · 90% range ${fmtPrice(low.bands.b90[0])}–${fmtPrice(low.bands.b90[1])}`);
  const issued = new Date(f.issued_at_utc);
  text("fc-issued", issued.toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Toronto" }));
  const record = document.getElementById("fc-record");
  if (record) {
    const [y, mo] = f.target_day.split("-");
    record.href = preview ? "#" : `${CONFIG.logRepo}/commits/main/forecasts/${y}/${mo}/${f.target_day}.json`;
  }
  const bands = [
    { key: "b98", label: "98% range", color: css("--band-98") },
    { key: "b90", label: "90% range", color: css("--band-90") },
    { key: "b50", label: "50% range", color: css("--band-50") },
  ];
  bandChart(document.getElementById("fc-chart"), { points, bands });
  table(document.getElementById("fc-table"), ["Hour (Toronto)", "HE (EST)", "Median", "50% range", "90% range", "98% range"],
    points.map((p) => [torontoHour(p.ts, { minute: "2-digit" }), String(p.he), fmtPrice(p.median),
      `${fmtPrice(p.bands.b50[0])}–${fmtPrice(p.bands.b50[1])}`, `${fmtPrice(p.bands.b90[0])}–${fmtPrice(p.bands.b90[1])}`,
      `${fmtPrice(p.bands.b98[0])}–${fmtPrice(p.bands.b98[1])}`]));
}

function renderTrackRecord(sc) {
  const body = document.getElementById("track-body");
  const scored = sc?.since_start?.days || 0;
  text("tr-published", sc ? String(sc.published_days) : "0");
  if (!sc || scored === 0) {
    text("tr-crps", "–"); text("tr-mae", "–"); text("tr-cov", "–");
    body.replaceChildren(Object.assign(document.createElement("div"), {
      className: "empty",
      textContent: "The track record starts with the first public forecast. Each day is scored once IESO publishes the actual day-ahead prices, roughly 13:30 Toronto time the day before delivery.",
    }));
    return;
  }
  const w = sc.last_30_days.days ? sc.last_30_days : sc.since_start;
  text("tr-crps", w.crps.toFixed(2));
  text("tr-mae", fmtPrice(w.mae));
  text("tr-cov", `${(w.coverage_90 * 100).toFixed(0)}%`);
  text("tr-notice", sc.notice || "");
  const hours = sc.recent_hours || []; // absent from scorecards written by older server code
  if (!hours.length) {
    body.replaceChildren();
    return;
  }
  const points = hours.map((h) => ({ ts: h.ts_utc, median: h.q50, actual: h.actual, bands: { b90: [h.q05, h.q95] } }));
  const chart = document.createElement("div");
  chart.className = "chart";
  chart.dataset.title = "Forecast vs actual day-ahead price, recent days";
  body.replaceChildren(chart);
  bandChart(chart, { points, bands: [{ key: "b90", label: "90% range", color: css("--band-90") }], dayDividers: true });
  document.getElementById("tr-legend").hidden = false;
}

// ------------------------------------------------------------------ research page
async function researchPage() {
  const data = await getJSON("data/site.json");
  const pr = data.preregistration;
  text("pr-date", pr.registered);
  text("pr-commit", pr.commit.slice(0, 7));
  text("pr-sha", pr.sha256);
  document.getElementById("pr-link").href = pr.url;
  for (const [k, v] of Object.entries(data.status)) text(`status-${k}`, v);

  const models = data.validation.models;
  barChart(document.getElementById("val-chart"), {
    rows: models.map((m) => ({ label: m.label, value: m.crps, detail: [
      { value: fmtPrice(m.mae), label: "mean absolute error" },
      { value: `${(m.coverage_90 * 100).toFixed(1)}%`, label: "inside 90% range" },
    ] })),
    format: (v) => v.toFixed(2),
  });
  table(document.getElementById("val-table"), ["Model", "CRPS", "MAE ($/MWh)", "rMAE", "80% coverage", "90% coverage", "90% width"],
    models.map((m) => [m.label, m.crps.toFixed(3), m.mae.toFixed(2), m.rmae.toFixed(3), `${(m.coverage_80 * 100).toFixed(1)}%`,
      `${(m.coverage_90 * 100).toFixed(1)}%`, fmtPrice(m.width_90)]));

  const stats = data.rq1_placebo.statistics || [];
  const q = data.rq1_placebo.quantiles_50_90_95 || {};
  const specs = [
    ["chow_f_mean", "Shift in average price (Chow F)", (v) => v.toFixed(0)],
    ["chow_f_log_sd", "Shift in volatility (Chow F)", (v) => v.toFixed(0)],
    ["ks_d", "Change in price distribution (KS distance)", (v) => v.toFixed(2)],
    ["abs_log_tail_ratio", "Change in spike tail (log tail-index ratio)", (v) => v.toFixed(2)],
  ];
  const grid = document.getElementById("placebo-grid");
  for (const [key, title, fmt] of specs) {
    const box = document.createElement("div");
    const h = document.createElement("h4");
    h.textContent = title;
    const chart = document.createElement("div");
    chart.className = "chart";
    chart.dataset.title = `${title}: 48 placebo dates`;
    box.append(h, chart);
    grid.appendChild(box);
    stripPlot(chart, {
      items: stats.map((s) => ({ label: String(s.placebo).slice(0, 7), value: s[key] })),
      threshold: q[key]?.[2] ?? 0,
      thresholdLabel: `95th pct ${fmt(q[key]?.[2] ?? 0)}`,
      format: fmt,
    });
  }
  table(document.getElementById("placebo-table"), ["Placebo date", ...specs.map((s) => s[1])],
    stats.map((s) => [String(s.placebo).slice(0, 10), ...specs.map(([k, , f]) => f(s[k]))]));
}

initTheme();
const page = document.body.dataset.page;
if (page === "forecast") forecastPage();
if (page === "research") researchPage();
