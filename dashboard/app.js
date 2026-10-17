// Dashboard client: everything shown is fetched from the backend (/api/*), which reads Neon Postgres.
// Review text is untrusted user content: every string goes through esc() or textContent.
"use strict";

const AREA_LABEL = { access: "Access", usability: "Usability", playback: "Playback", downloads: "Downloads",
  catalog: "Catalog", billing: "Billing", support: "Support", other: "Other / general", billing_support: "Billing + support" };
const TREND_AREAS = ["usability", "billing_support", "playback", "access"];   // the brief's four candidate areas, fixed order
const $ = (s) => document.querySelector(s);
const fmt = (n) => Number(n).toLocaleString("en-US");
const pct = (x, d = 0) => `${(100 * x).toFixed(d)}%`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

async function api(path) {
  const r = await fetch(`/api/${path}`);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

// ---- tooltip -------------------------------------------------------------------------------
const tip = $("#tip");
function showTip(html, ev) {
  tip.innerHTML = html;
  tip.style.display = "block";
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
  if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
const hideTip = () => { tip.style.display = "none"; };

// ---- KPIs ----------------------------------------------------------------------------------
function kpis(m) {
  const c = m.coverage, v = m.verification, cost = m.cost;
  const nonEmptyQ = c.quarantined - (c.quarantine_reasons.empty_review_text || 0);
  const tiles = [
    ["Reviews accounted for", fmt(c.completed + c.quarantined), `of ${fmt(c.source_rows)} source rows`],
    ["Classified", fmt(c.completed), `${pct(c.completed / c.nonempty, 3)} of ${fmt(c.nonempty)} nonempty`],
    ["Quarantined", fmt(c.quarantined), `${c.quarantine_reasons.empty_review_text || 0} empty texts · ${fmt(nonEmptyQ)} unresolved`],
    ["Exact-text reuse", fmt(c.exact_text_reuse_records), `${fmt(c.distinct_texts)} distinct texts classified`],
    ["Verifier agreement", `${pct(v.topic_agreement)} / ${pct(v.intent_agreement)}`, `topic / intent · n=${fmt(v.verified)} blind re-labels`],
    ["API spend (from usage)", `$${cost.api_usd_estimated_from_usage.toFixed(2)}`, `${fmt(Object.values(cost.calls).reduce((a, b) => a + b, 0))} calls · ${(cost.wall_clock_s / 3600).toFixed(1)} h wall clock`],
  ];
  if (m.golden) tiles[4] = ["Golden-50 agreement", `${pct(m.golden.agreement_strict.topic)} / ${pct(m.golden.agreement_strict.intent)}`,
    `topic / intent vs human labels · severity MAE ${m.golden.severity_mae_vs_primary}`];
  $("#kpis").innerHTML = tiles.map(([l, val, n]) =>
    `<div class="card kpi"><div class="label">${esc(l)}</div><div class="value">${esc(val)}</div><div class="note">${esc(n)}</div></div>`).join("");
}

// ---- memo ----------------------------------------------------------------------------------
let CLAIMS = {};
function renderMemo(memo) {
  if (!memo) { $("#memo").textContent = "No memo stored."; return; }
  $("#rec-area").textContent = `Priority: ${AREA_LABEL[memo.priority_area] || memo.priority_area || "—"}`;
  $("#rec-headline").textContent = memo.headline || "";
  const ok = memo.status === "validated" && !(memo.validation_problems || []).length;
  $("#memo-badge").innerHTML = `<span class="dot" style="background:var(${ok ? "--good" : "--critical"})"></span>${ok ? "✓ code-validated" : "⚠ validation issues"} · ${esc(memo.model)}`;
  const lines = esc(memo.memo_markdown || "").split("\n");
  let html = "", list = null;
  const inline = (t) => t
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(C\d{3})\]/g, (_, id) => `<button class="claim" data-claim="${id}">${id}</button>`)
    .replace(/\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/g, (id) => `<button class="rid" data-rid="${id}">${id.slice(0, 8)}…</button>`);
  for (const raw of lines) {
    const line = raw.trim();
    const li = line.match(/^(?:[-*]|\d+\.)\s+(.*)$/);
    if (li) { if (!list) { list = "ul"; html += "<ul>"; } html += `<li>${inline(li[1])}</li>`; continue; }
    if (list) { html += "</ul>"; list = null; }
    if (/^#{1,6}\s/.test(line)) html += `<h3>${inline(line.replace(/^#+\s*/, ""))}</h3>`;
    else if (line) html += `<p>${inline(line)}</p>`;
  }
  if (list) html += "</ul>";
  $("#memo").innerHTML = html;
}

document.addEventListener("mouseover", (ev) => {
  const b = ev.target.closest(".claim");
  if (!b) return;
  const c = CLAIMS[b.dataset.claim];
  if (!c) return showTip(`<b>${esc(b.dataset.claim)}</b><br>not found in claims table`, ev);
  const match = String(c.value) === String(c.db_value);
  showTip(`<b>${esc(c.claim_id)}</b> · ${esc(c.metric)} = <b>${esc(c.value)}</b><br>issue <b>${esc(c.name || c.issue_id)}</b> (rank ${esc(c.rank)})<br>
    <span style="color:var(${match ? "--good" : "--critical"})">${match ? "✓ matches" : "✗ differs from"} the stored ranking value ${esc(c.db_value)}</span>`, ev);
});
document.addEventListener("mouseout", (ev) => { if (ev.target.closest(".claim")) hideTip(); });
document.addEventListener("click", (ev) => {
  const r = ev.target.closest("[data-rid]");
  if (r) openReview(r.dataset.rid);
  const c = ev.target.closest(".claim");
  if (c && CLAIMS[c.dataset.claim]) selectIssue(CLAIMS[c.dataset.claim].issue_id, true);
});

async function openReview(id) {
  const body = $("#review-body");
  body.innerHTML = `<div class="muted">Loading ${esc(id)}…</div>`;
  $("#review-dialog").showModal();
  try {
    const { review: r } = await api(`review?id=${encodeURIComponent(id)}`);
    const rows = [["Review ID", r.review_id], ["Status", r.status + (r.reason ? ` (${r.reason})` : "")],
      ["Topic / subtopic", `${r.topic}.${r.subtopic}`], ["Intent", r.intent], ["Severity", r.severity], ["Sentiment", r.sentiment],
      ["Needs review", r.needs_review ? `yes — ${r.review_note || ""}` : "no"], ["Issue", r.issue_id || "— (not a complaint)"],
      ["Entities", (r.entities || []).join(", ") || "—"], ["Stars (metadata only)", r.review_rating],
      ["Timestamp", r.review_timestamp], ["App version", r.app_version || "missing"],
      ["Exact-text reuse of", r.cache_source_id || "— (labelled by its own model call)"], ["Label config", r.label_config]];
    body.innerHTML = `<h2>Saved classification</h2><blockquote class="quote">${esc(r.evidence_quote)}</blockquote>
      <div class="muted" style="margin-bottom:10px">Evidence quote: an exact substring of the original review text, checked by code.</div>
      <div class="kv">${rows.map(([k, v]) => `<div>${esc(k)}</div><div>${esc(v)}</div>`).join("")}</div>`;
  } catch (e) { body.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// ---- area bars (single series, one hue) ----------------------------------------------------
let TOPICS = [], AREA_METRIC = "complaint_records";
function renderAreas() {
  const rows = TOPICS.filter((t) => t.area !== "billing_support").map((t) => ({ ...t, v: Number(t[AREA_METRIC]) }))
    .sort((a, b) => b.v - a.v);
  const max = Math.max(...rows.map((r) => r.v), 1);
  const W = 520, rowH = 30, left = 120, right = 70, H = rows.length * rowH + 8;
  const bars = rows.map((r, i) => {
    const w = Math.max(2, (W - left - right) * r.v / max), y = i * rowH + 6;
    return `<g class="bar" data-i="${i}">
      <rect x="0" y="${y - 4}" width="${W}" height="${rowH}" fill="transparent"/>
      <text x="${left - 10}" y="${y + 13}" text-anchor="end">${esc(AREA_LABEL[r.area])}</text>
      ${w >= 8 ? `<path d="M${left},${y + 3} h${w - 4} a4,4 0 0 1 4,4 v6 a4,4 0 0 1 -4,4 h-${w - 4} z" fill="var(--series-1)"/>`
               : `<rect x="${left}" y="${y + 3}" width="${w}" height="14" fill="var(--series-1)"/>`}
      <text x="${left + w + 6}" y="${y + 13}" style="fill:var(--text-primary)">${fmt(r.v)}</text></g>`;
  }).join("");
  $("#areas").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Bar chart of ${AREA_METRIC} by area">
    <line x1="${left}" x2="${left}" y1="0" y2="${H}" stroke="var(--line)"/>${bars}</svg>`;
  $("#areas").querySelectorAll(".bar").forEach((g) => {
    const r = rows[g.dataset.i];
    g.addEventListener("mousemove", (ev) => showTip(`<b>${esc(AREA_LABEL[r.area])}</b><br>complaints <b>${fmt(r.complaint_records)}</b>
      (${esc(r.share_of_all_complaints)} of all)<br>severity sum <b>${fmt(r.severity_sum)}</b> · mean ${esc(r.mean_severity)}<br>
      severity ≥ 4 <b>${fmt(r.sev4plus_complaints)}</b> · cancellation intent <b>${fmt(r.cancellation_records)}</b><br>
      completed records ${fmt(r.completed_records)}`, ev));
    g.addEventListener("mouseleave", hideTip);
  });
  const cols = ["area", "completed_records", "complaint_records", "share_of_all_complaints", "severity_sum", "mean_severity", "sev4plus_complaints", "cancellation_records"];
  $("#areas-table").innerHTML = `<table><thead><tr>${cols.map((c) => `<th>${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr></thead><tbody>${
    TOPICS.map((t) => `<tr>${cols.map((c) => `<td>${esc(c === "area" ? AREA_LABEL[t[c]] : t[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
document.querySelectorAll("[data-metric]").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("[data-metric]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  AREA_METRIC = b.dataset.metric;
  renderAreas();
}));

// ---- ranking -------------------------------------------------------------------------------
let ISSUES = [], FILTER = "all", SELECTED = null;
function renderRanking() {
  const rows = ISSUES.filter((i) => FILTER === "all" || i.subtopic !== "general");
  const max = Math.max(...ISSUES.map((i) => i.priority_score), 1);
  $("#ranking tbody").innerHTML = rows.map((i) => `<tr data-id="${esc(i.issue_id)}" class="${i.issue_id === SELECTED ? "sel" : ""}">
    <td class="num">${i.rank}</td>
    <td><div class="issue-name">${esc(i.name)}</div><div class="issue-id">${esc(i.issue_id)}</div></td>
    <td>${esc(AREA_LABEL[i.topic])}</td>
    <td class="num">${fmt(i.complaint_count)}</td>
    <td class="num">${esc(i.mean_severity)}</td>
    <td class="num"><div class="scorebar"><span>${fmt(i.priority_score)}</span><div class="track"><div class="fill" style="width:${(100 * i.priority_score / max).toFixed(1)}%"></div></div></div></td>
    <td class="num">${fmt(i.cancellation_count)}</td>
    <td class="flag">${i.coherence === "coherent" ? "coherent" : `⚠ ${esc(i.coherence)}`}</td></tr>`).join("");
  $("#ranking tbody").querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", () => selectIssue(tr.dataset.id)));
}
document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  FILTER = b.dataset.filter;
  renderRanking();
}));

async function selectIssue(id, scroll) {
  SELECTED = id;
  renderRanking();
  const box = $("#drill");
  box.hidden = false;
  box.innerHTML = `<div class="muted">Loading evidence for ${esc(id)}…</div>`;
  if (scroll) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const { issue: i, evidence } = await api(`issue?id=${encodeURIComponent(id)}&limit=12`);
    const total = i.sev.reduce((a, b) => a + b, 0) || 1;
    const sevColors = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5"];
    box.innerHTML = `<h2>${esc(i.name)} <span class="issue-id">${esc(i.issue_id)} · rank ${i.rank}</span></h2>
      <div class="sub">${esc(i.summary)}</div>
      <div class="legend" style="margin-top:10px">${i.sev.map((n, k) => `<span><i class="sq" style="background:var(${sevColors[k]})"></i>severity ${k + 1}: ${fmt(n)}</span>`).join("")}</div>
      <div class="sevstack" role="img" aria-label="Severity distribution">${i.sev.map((n, k) => n ? `<div style="width:${100 * n / total}%;background:var(${sevColors[k]})" title="severity ${k + 1}: ${fmt(n)}"></div>` : "").join("")}</div>
      <div class="muted">${fmt(i.complaint_count)} complaints · severity sum ${fmt(i.severity_sum)} · mean ${esc(i.mean_severity)} · ${fmt(i.needs_review_count)} flagged needs_review · grouping check: ${esc(i.coherence)}</div>
      <h3 style="font-size:14px;margin:14px 0 4px">Highest-severity member reviews</h3>
      ${evidence.map((r) => `<div class="quote">${esc(r.evidence_quote)}<div class="meta">
        <button class="rid" data-rid="${esc(r.review_id)}">${esc(r.review_id.slice(0, 8))}…</button>
        <span>${esc(r.intent)} · severity ${r.severity}</span><span>${esc(String(r.review_timestamp || "").slice(0, 10))}</span>
        ${r.needs_review ? "<span>⚑ needs review</span>" : ""}${r.cache_source_id ? "<span>exact-text reuse</span>" : ""}</div></div>`).join("")}`;
  } catch (e) { box.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// ---- trend lines (4 fixed categorical series, legend + direct labels + crosshair) ----------
function renderTrend(rows) {
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const by = {};
  rows.forEach((r) => { by[`${r.month}|${r.area}`] = r; });
  // Denominator: all complaint + cancellation records that month (sum over the eight base topics).
  const BASE = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"];
  const total = Object.fromEntries(months.map((m) => [m, BASE.reduce((a, t) => a + (by[`${m}|${t}`]?.complaints || 0), 0)]));
  const series = TREND_AREAS.map((a, k) => ({ area: a, color: `var(--series-${k + 1})`,
    pts: months.map((m) => { const r = by[`${m}|${a}`]; return r && total[m] ? { m, v: r.complaints / total[m], r, d: total[m] } : null; }) }));
  const W = 1100, H = 300, L = 44, R = 130, T = 12, B = 30;
  const max = Math.max(0.05, ...series.flatMap((s) => s.pts.filter(Boolean).map((p) => p.v))) * 1.1;
  const x = (i) => L + (W - L - R) * (months.length > 1 ? i / (months.length - 1) : 0.5);
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const grid = ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" stroke="var(--grid)"/>
    <text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${pct(t)}</text>`).join("");
  const xl = months.map((m, i) => (i % 3 === 0 || i === months.length - 1)
    ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(m)}</text>` : "").join("");
  // Direct end labels, nudged apart so they never collide (min 13px gap).
  const ends = series.map((s, k) => { const i = s.pts.map((p, j) => p ? j : -1).filter((j) => j >= 0).pop();
    return { k, i, y: i == null ? null : y(s.pts[i].v) }; }).filter((e) => e.y != null).sort((a, b) => a.y - b.y);
  ends.forEach((e, n) => { e.ly = n ? Math.max(e.y, ends[n - 1].ly + 13) : e.y; });
  const lines = series.map((s, k) => {
    const d = s.pts.map((p, i) => p ? `${i && s.pts[i - 1] ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}` : "").join("");
    const e = ends.find((q) => q.k === k);
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>
      ${e ? `<circle cx="${x(e.i)}" cy="${e.y}" r="4" fill="${s.color}" stroke="var(--surface-1)" stroke-width="2"/>
      <text x="${x(e.i) + 10}" y="${e.ly + 4}" style="fill:var(--text-primary)">${esc(AREA_LABEL[s.area])}</text>` : ""}`;
  }).join("");
  $("#trend").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Monthly complaint share by area">
    ${grid}${xl}${lines}<line id="xh" y1="${T}" y2="${H - B}" stroke="var(--text-muted)" stroke-dasharray="3 3" visibility="hidden"/>
    <rect id="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;
  $("#trend-legend").innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(AREA_LABEL[s.area])}</span>`).join("");
  const svg = $("#trend svg"), hit = $("#hit"), xh = $("#xh");
  hit.addEventListener("mousemove", (ev) => {
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const i = Math.max(0, Math.min(months.length - 1, Math.round((p.x - L) / ((W - L - R) / Math.max(1, months.length - 1)))));
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
    const partial = i === 0 || i === months.length - 1 ? " (partial month)" : "";
    showTip(`<b>${esc(months[i])}</b>${partial}<br>` + series.map((s) => { const q = s.pts[i];
      return `<span style="display:inline-block;width:10px;height:3px;background:${s.color};vertical-align:middle;margin-right:6px"></span>${esc(AREA_LABEL[s.area])}: <b>${q ? pct(q.v, 1) : "—"}</b> ${q ? `(${fmt(q.r.complaints)} of ${fmt(q.d)} complaints)` : ""}`; }).join("<br>"), ev);
  });
  hit.addEventListener("mouseleave", () => { hideTip(); xh.setAttribute("visibility", "hidden"); });
  $("#trend-table").innerHTML = `<table><thead><tr><th>month</th><th class="num">all complaints</th>${TREND_AREAS.map((a) => `<th class="num">${esc(AREA_LABEL[a])}</th>`).join("")}</tr></thead><tbody>${
    months.map((m) => `<tr><td>${esc(m)}${m === months[0] || m === months[months.length - 1] ? " (partial)" : ""}</td><td class="num">${fmt(total[m])}</td>${TREND_AREAS.map((a) => { const r = by[`${m}|${a}`];
      return `<td class="num">${r && total[m] ? `${fmt(r.complaints)} (${pct(r.complaints / total[m], 1)})` : "—"}</td>`; }).join("")}</tr>`).join("")}</tbody></table>`;
}

// ---- boot ----------------------------------------------------------------------------------
(async function boot() {
  try {
    const [ov, rk, mm, tr] = await Promise.all([api("overview"), api("ranking"), api("memo"), api("trend")]);
    const m = ov.metrics;
    $("#subtitle").textContent = `Run ${ov.run.run_id} · ${fmt(m.coverage.source_rows)} Google Play reviews, ${m.run.review_window[0].slice(0, 10)} → ${m.run.review_window[1].slice(0, 10)} · label config ${ov.run.label_config}`;
    $("#source-badge").innerHTML = `<span class="dot" style="background:var(--good)"></span>live from Postgres · loaded ${esc(new Date(ov.run.loaded_at).toLocaleString())}`;
    kpis(m);
    CLAIMS = Object.fromEntries(mm.claims.map((c) => [c.claim_id, c]));
    renderMemo(mm.memo);
    TOPICS = ov.topics;
    renderAreas();
    ISSUES = rk.issues;
    renderRanking();
    renderTrend(tr.months);
    if (ISSUES.length) selectIssue(ISSUES.find((i) => i.subtopic !== "general")?.issue_id || ISSUES[0].issue_id);
  } catch (e) {
    $("#subtitle").innerHTML = `<span class="err">Could not load data from the backend: ${esc(e.message)}</span>`;
    $("#source-badge").innerHTML = `<span class="dot" style="background:var(--critical)"></span>backend unavailable`;
  }
})();
