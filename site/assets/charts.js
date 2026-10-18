// Small, dependency-free SVG charts for the GridCast site.
// Rules followed (dataviz method): 2px lines, recessive hairline grid, one y-axis, bands
// as a one-hue sequential ramp, crosshair + tooltip on line/area, per-mark tooltips on bars
// and dots, text in ink tokens (never the series colour), a table view next to each chart.

const NS = "http://www.w3.org/2000/svg";
const TZ = "America/Toronto";

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

export function fmtPrice(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return `$${v.toLocaleString("en-CA", { maximumFractionDigits: v >= 100 ? 0 : 1 })}`;
}

export function torontoHour(iso, opts = {}) {
  return new Date(iso).toLocaleTimeString("en-CA", { hour: "numeric", timeZone: TZ, ...opts });
}

export function torontoDay(iso) {
  return new Date(iso).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric", timeZone: TZ });
}

function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || 1;
  const raw = span / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => span / s <= count) || 10 * pow;
  const start = Math.floor(lo / step) * step;
  const ticks = [];
  // the last tick always reaches hi, so no mark can fall outside the axis
  for (let t = start; ; t += step) {
    ticks.push(Math.round(t * 1e6) / 1e6);
    if (t >= hi - step * 1e-9) break;
  }
  return ticks;
}

function makeTooltip(container) {
  const tip = document.createElement("div");
  tip.className = "tooltip";
  tip.hidden = true;
  tip.setAttribute("role", "status");
  container.appendChild(tip);
  return tip;
}

function setTooltip(tip, head, rows) {
  tip.replaceChildren();
  const h = document.createElement("div");
  h.className = "t-head";
  h.textContent = head;
  tip.appendChild(h);
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "t-row";
    if (r.color) {
      const key = document.createElement("span");
      key.className = "t-key";
      key.style.background = r.color;
      if (r.band) key.style.height = "8px";
      row.appendChild(key);
    }
    const strong = document.createElement("strong");
    strong.textContent = r.value;
    row.appendChild(strong);
    const label = document.createElement("span");
    label.textContent = ` ${r.label}`;
    row.appendChild(label);
    tip.appendChild(row);
  }
}

function placeTooltip(container, tip, x, y) {
  const w = container.clientWidth;
  tip.hidden = false;
  const tw = tip.offsetWidth;
  const left = x + 14 + tw > w ? x - tw - 14 : x + 14;
  tip.style.left = `${Math.max(0, left)}px`;
  tip.style.top = `${Math.max(0, y - 10)}px`;
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Band chart: nested quantile bands + median line, optional actual line.
 * points: [{ts, bands: {key: [lo, hi]}, median, actual}]
 * bands: [{key, label, color}] outermost first.
 */
export function bandChart(container, { points, bands, actualLabel = "Actual price", dayDividers = false, height = 300 }) {
  const draw = () => {
    container.replaceChildren();
    const width = Math.max(container.clientWidth, 280);
    const m = { top: 12, right: 14, bottom: 30, left: 48 };
    const values = points.flatMap((p) => [...Object.values(p.bands).flat(), p.median, p.actual]).filter((v) => v !== null && v !== undefined);
    const ticks = niceTicks(Math.min(...values), Math.max(...values));
    const y0 = ticks[0];
    const y1 = ticks[ticks.length - 1];
    const x = (i) => m.left + (points.length === 1 ? 0 : (i / (points.length - 1)) * (width - m.left - m.right));
    const y = (v) => m.top + (1 - (v - y0) / (y1 - y0 || 1)) * (height - m.top - m.bottom);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: "0" }, container);
    el("title", {}, svg).textContent = container.dataset.title || "Forecast chart";

    for (const t of ticks) {
      el("line", { class: t === y0 ? "baseline" : "grid", x1: m.left, x2: width - m.right, y1: y(t), y2: y(t) }, svg);
      el("text", { x: m.left - 8, y: y(t) + 4, "text-anchor": "end" }, svg).textContent = `$${t}`;
    }
    // x ticks: hours (every 3, or 6 on narrow screens), or day boundaries for multi-day
    // views, thinned so labels never collide
    const plotW = width - m.left - m.right;
    const hourEvery = plotW < 460 ? 6 : 3;
    const midnights = points.map((p, i) => [p, i]).filter(([p]) =>
      Number(new Date(p.ts).toLocaleString("en-CA", { hour: "numeric", hour12: false, timeZone: TZ })) % 24 === 0);
    const dayEvery = Math.max(1, Math.ceil((midnights.length * 84) / plotW));
    if (dayDividers) {
      midnights.forEach(([p, i], k) => {
        el("line", { class: "grid", x1: x(i), x2: x(i), y1: m.top, y2: height - m.bottom }, svg);
        if (k % dayEvery === 0) el("text", { x: x(i), y: height - 10, "text-anchor": "middle" }, svg).textContent = torontoDay(p.ts);
      });
    } else {
      points.forEach((p, i) => {
        if (i % hourEvery === 0) el("text", { x: x(i), y: height - 10, "text-anchor": "middle" }, svg).textContent = torontoHour(p.ts);
      });
    }

    const area = (key) => {
      const top = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.bands[key][1])}`).join("");
      const bottom = points.map((p, i) => [i, p]).reverse().map(([i, p]) => `L${x(i)},${y(p.bands[key][0])}`).join("");
      return `${top}${bottom}Z`;
    };
    for (const b of bands) el("path", { d: area(b.key), fill: b.color }, svg);
    const line = (acc) => {
      let d = "";
      let pen = false;
      points.forEach((p, i) => {
        const v = acc(p);
        if (v === null || v === undefined) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i)},${y(v)}`;
        pen = true;
      });
      return d;
    };
    el("path", { d: line((p) => p.median), fill: "none", stroke: css("--median"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    const hasActual = points.some((p) => p.actual !== null && p.actual !== undefined);
    if (hasActual) el("path", { d: line((p) => p.actual), fill: "none", stroke: css("--actual"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);

    // hover layer
    const cross = el("line", { class: "crosshair", y1: m.top, y2: height - m.bottom, visibility: "hidden" }, svg);
    const dotMedian = el("circle", { r: 4, fill: css("--median"), stroke: css("--surface"), "stroke-width": 2, visibility: "hidden" }, svg);
    const dotActual = el("circle", { r: 4, fill: css("--actual"), stroke: css("--surface"), "stroke-width": 2, visibility: "hidden" }, svg);
    const tip = makeTooltip(container);
    let current = points.length - 1;
    const show = (i) => {
      current = Math.max(0, Math.min(points.length - 1, i));
      const p = points[current];
      cross.setAttribute("x1", x(current));
      cross.setAttribute("x2", x(current));
      cross.setAttribute("visibility", "visible");
      dotMedian.setAttribute("cx", x(current));
      dotMedian.setAttribute("cy", y(p.median));
      dotMedian.setAttribute("visibility", "visible");
      const rows = [{ value: fmtPrice(p.median), label: "median forecast", color: css("--median") }];
      for (const b of [...bands].reverse()) {
        rows.push({ value: `${fmtPrice(p.bands[b.key][0])}–${fmtPrice(p.bands[b.key][1])}`, label: b.label, color: b.color, band: true });
      }
      if (p.actual !== null && p.actual !== undefined) {
        dotActual.setAttribute("cx", x(current));
        dotActual.setAttribute("cy", y(p.actual));
        dotActual.setAttribute("visibility", "visible");
        rows.unshift({ value: fmtPrice(p.actual), label: actualLabel.toLowerCase(), color: css("--actual") });
      } else {
        dotActual.setAttribute("visibility", "hidden");
      }
      const start = torontoHour(p.ts, { minute: "2-digit" });
      const head = `${torontoDay(p.ts)} · ${start} Toronto${p.he ? ` · HE ${p.he} (EST)` : ""}`;
      setTooltip(tip, head, rows);
      const scale = container.clientWidth / width;
      placeTooltip(container, tip, x(current) * scale, y(p.median) * scale);
    };
    const hide = () => {
      cross.setAttribute("visibility", "hidden");
      dotMedian.setAttribute("visibility", "hidden");
      dotActual.setAttribute("visibility", "hidden");
      tip.hidden = true;
    };
    const hit = el("rect", { x: m.left, y: m.top, width: width - m.left - m.right, height: height - m.top - m.bottom, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", (ev) => {
      const box = svg.getBoundingClientRect();
      const px = ((ev.clientX - box.left) / box.width) * width;
      show(Math.round(((px - m.left) / (width - m.left - m.right)) * (points.length - 1)));
    });
    hit.addEventListener("pointerleave", hide);
    svg.addEventListener("focus", () => show(current));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowLeft") { show(current - 1); ev.preventDefault(); }
      if (ev.key === "ArrowRight") { show(current + 1); ev.preventDefault(); }
    });
  };
  draw();
  new ResizeObserver(() => draw()).observe(container);
}

/** Horizontal bars, one series (no legend: the title names it). rows: [{label, value, detail}] */
export function barChart(container, { rows, format = (v) => v.toFixed(2), unitLabel = "" }) {
  const draw = () => {
    container.replaceChildren();
    const width = Math.max(container.clientWidth, 280);
    const labelW = Math.min(170, width * 0.38);
    const rowH = 34;
    const barH = 20;
    const m = { top: 4, right: 56, bottom: 26, left: labelW };
    const height = m.top + rows.length * rowH + m.bottom;
    const max = Math.max(...rows.map((r) => r.value));
    const ticks = niceTicks(0, max, 4);
    const xMax = ticks[ticks.length - 1];
    const x = (v) => m.left + (v / xMax) * (width - m.left - m.right);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" }, container);
    el("title", {}, svg).textContent = container.dataset.title || "Bar chart";
    for (const t of ticks) {
      el("line", { class: t === 0 ? "baseline" : "grid", x1: x(t), x2: x(t), y1: m.top, y2: height - m.bottom }, svg);
      el("text", { x: x(t), y: height - 8, "text-anchor": "middle" }, svg).textContent = `${t}${unitLabel}`;
    }
    const tip = makeTooltip(container);
    rows.forEach((r, i) => {
      const cy = m.top + i * rowH + rowH / 2;
      el("text", { x: m.left - 10, y: cy + 4, "text-anchor": "end", class: "label-strong" }, svg).textContent = r.label;
      const x0 = x(0);
      const x1 = x(r.value);
      const top = cy - barH / 2;
      const rad = Math.min(4, (x1 - x0) / 2);
      const d = `M${x0},${top}H${x1 - rad}Q${x1},${top} ${x1},${top + rad}V${top + barH - rad}Q${x1},${top + barH} ${x1 - rad},${top + barH}H${x0}Z`;
      const bar = el("path", { d, fill: css("--bar"), tabindex: "0" }, svg);
      el("text", { x: x1 + 6, y: cy + 4, class: "label-strong" }, svg).textContent = format(r.value);
      const hitbox = el("rect", { x: 0, y: cy - rowH / 2, width, height: rowH, fill: "transparent" }, svg);
      const on = () => {
        bar.setAttribute("fill", css("--bar-hover"));
        setTooltip(tip, r.label, [{ value: format(r.value), label: unitLabel || "" }, ...(r.detail || []).map((d2) => ({ value: d2.value, label: d2.label }))]);
        const scale = container.clientWidth / width;
        placeTooltip(container, tip, x1 * scale, cy * scale);
      };
      const off = () => { bar.setAttribute("fill", css("--bar")); tip.hidden = true; };
      hitbox.addEventListener("pointerenter", on);
      hitbox.addEventListener("pointerleave", off);
      bar.addEventListener("focus", on);
      bar.addEventListener("blur", off);
    });
  };
  draw();
  new ResizeObserver(() => draw()).observe(container);
}

/** One-dimensional dot strip with a reference line (placebo distributions). */
export function stripPlot(container, { items, threshold, thresholdLabel, format = (v) => v.toFixed(2) }) {
  const draw = () => {
    container.replaceChildren();
    const width = Math.max(container.clientWidth, 220);
    const m = { top: 18, right: 14, bottom: 26, left: 14 };
    const values = items.map((d) => d.value);
    const ticks = niceTicks(0, Math.max(...values, threshold), 4);
    const xMax = ticks[ticks.length - 1];
    const x = (v) => m.left + (v / xMax) * (width - m.left - m.right);
    // dot stacking: each dot keeps its exact x and takes the lowest row where it clears
    // the previous dot by a full diameter, so no two dots overlap
    const step = 12; // 10px dots plus a 2px surface ring
    const rowEnd = [];
    const placed = [...items].sort((a, b) => a.value - b.value).map((d) => {
      const cx = x(d.value);
      let level = rowEnd.findIndex((end) => cx - end >= step);
      if (level === -1) level = rowEnd.length;
      rowEnd[level] = cx;
      return { ...d, cx, level };
    });
    const levels = Math.max(...placed.map((p) => p.level)) + 1;
    const height = m.top + levels * step + 12 + m.bottom;
    const base = height - m.bottom - 8;
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" }, container);
    el("title", {}, svg).textContent = container.dataset.title || "Distribution";
    el("line", { class: "baseline", x1: m.left, x2: width - m.right, y1: height - m.bottom, y2: height - m.bottom }, svg);
    for (const t of ticks) el("text", { x: x(t), y: height - 8, "text-anchor": "middle" }, svg).textContent = format(t);
    const tx = x(threshold);
    el("line", { x1: tx, x2: tx, y1: m.top - 6, y2: height - m.bottom, stroke: css("--ink-2"), "stroke-width": 1 }, svg);
    el("text", { x: Math.min(tx + 4, width - m.right), y: m.top - 8, "text-anchor": tx > width * 0.7 ? "end" : "start", class: "label-strong" }, svg).textContent = thresholdLabel;
    const tip = makeTooltip(container);
    for (const p of placed) {
      const cy = base - p.level * step;
      el("circle", { cx: p.cx, cy, r: 5, fill: css("--dot"), stroke: css("--surface"), "stroke-width": 2 }, svg);
      const hit = el("circle", { cx: p.cx, cy, r: 12, fill: "transparent", tabindex: "0" }, svg);
      const on = () => {
        setTooltip(tip, `Placebo date ${p.label}`, [{ value: format(p.value), label: "" }]);
        const scale = container.clientWidth / width;
        placeTooltip(container, tip, p.cx * scale, cy * scale);
      };
      hit.addEventListener("pointerenter", on);
      hit.addEventListener("pointerleave", () => { tip.hidden = true; });
      hit.addEventListener("focus", on);
      hit.addEventListener("blur", () => { tip.hidden = true; });
    }
  };
  draw();
  new ResizeObserver(() => draw()).observe(container);
}
