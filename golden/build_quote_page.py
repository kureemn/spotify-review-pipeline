"""Build golden/quote_check.html: the human checks whether each model evidence quote supports the model's label.

Shows the review, the model's label and the quote highlighted inside the review. No prior judgement is shown.
Exports golden_quote_check.csv.

    python3 golden/build_quote_page.py && open golden/quote_check.html
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cases = [json.loads(l) for l in open(ROOT / "evals" / "golden" / "golden_cases.jsonl", encoding="utf-8")]
items = [{"review_id": c["review_id"], "text": c["text"], "quote": c.get("quote_predicted", ""),
          "label": f"{c['predicted_topic']}.{c.get('predicted_subtopic')}", "intent": c["predicted_intent"],
          "severity": c["predicted_severity"]} for c in cases]

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quote Support Check</title>
<style>
  :root { --bg:#f6f5f1; --panel:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#dedcd5; --accent:#1f6f4a; --soft:#e3f1e9; --mark:#fff1a8; }
  @media (prefers-color-scheme: dark) { :root { --bg:#151514; --panel:#1f1f1d; --ink:#ecebe6; --muted:#a3a29c; --line:#3a3936; --accent:#5cc493; --soft:#1d3a2c; --mark:#5c4d00; } }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  header { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; padding:12px 16px; background:var(--panel); border-bottom:1px solid var(--line); position:sticky; top:0; }
  h1 { font-size:16px; margin:0; }
  main { max-width:820px; margin:0 auto; padding:16px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:12px; }
  .review { white-space:pre-wrap; font-size:16px; padding:12px; background:var(--bg); border-radius:8px; border:1px solid var(--line); }
  mark { background:var(--mark); color:inherit; border-radius:3px; padding:0 2px; }
  .label { margin:10px 0; font-size:14px; } .label b { font-family: ui-monospace, Menlo, monospace; }
  .opts { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  button { font:inherit; border:1px solid var(--line); background:var(--panel); color:var(--ink); border-radius:8px; padding:8px 14px; cursor:pointer; }
  button[aria-pressed="true"] { background:var(--soft); border-color:var(--accent); color:var(--accent); font-weight:600; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  input[type=text] { width:100%; margin-top:8px; font:inherit; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); }
  .muted { color:var(--muted); font-size:13px; }
  .full { color:var(--muted); font-size:12px; }
</style></head>
<body>
<header><h1>Quote support check · <span id="prog"></span></h1>
  <button class="primary" onclick="exportCSV()">Export golden_quote_check.csv</button></header>
<main>
  <div class="card"><b>For each review:</b> does the <mark>highlighted quote</mark> give enough evidence for the model's label
  (topic, intent and severity)?<br><span class="muted"><b>Supports</b>: the quote alone justifies the label.
  <b>Partial</b>: it supports some of the label (e.g. the topic but not the severity, or it relies on context outside the quote).
  <b>Does not</b>: the quote doesn't back the label. You're judging the <i>quote</i>, not whether you agree with the label.</span></div>
  <div id="list"></div>
</main>
<script>
const ITEMS = /*__ITEMS__*/[];
const KEY = "quote-check-v1";
let J = {}; try { J = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
function save() { try { localStorage.setItem(KEY, JSON.stringify(J)); } catch (e) {} prog(); }
function prog() { document.getElementById("prog").textContent = `${ITEMS.filter(i => J[i.review_id]?.v).length}/${ITEMS.length} done`; }
function highlight(text, q) {
  const i = q ? text.indexOf(q) : -1;
  if (i < 0) return esc(text);
  return esc(text.slice(0, i)) + "<mark>" + esc(q) + "</mark>" + esc(text.slice(i + q.length));
}
function render() {
  document.getElementById("list").innerHTML = ITEMS.map((it, k) => {
    const j = J[it.review_id] || {};
    const full = it.quote === it.text;
    return `<div class="card" id="c${k}"><div class="muted">${k + 1} / ${ITEMS.length} · ${esc(it.review_id.slice(0, 8))}</div>
      <div class="review">${highlight(it.text, it.quote)}</div>
      ${full ? '<div class="full">(short review: the whole text is the quote)</div>' : ""}
      <div class="label">Model label: <b>${esc(it.label)}</b> · intent <b>${esc(it.intent)}</b> · severity <b>${esc(it.severity)}</b></div>
      <div class="opts">${["supports", "partial", "does_not"].map(v =>
        `<button data-k="${k}" data-v="${v}" aria-pressed="${j.v === v}">${{supports:"Supports",partial:"Partial",does_not:"Does not support"}[v]}</button>`).join("")}</div>
      <input type="text" data-c="${k}" placeholder="optional comment (e.g. why partial)" value="${esc(j.c || "")}"></div>`;
  }).join("");
  prog();
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-v]"); if (!b) return;
  const it = ITEMS[b.dataset.k];
  J[it.review_id] = { ...(J[it.review_id] || {}), v: b.dataset.v, at: new Date().toISOString() };
  save();
  b.parentElement.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
});
document.addEventListener("input", (e) => {
  const t = e.target.closest("input[data-c]"); if (!t) return;
  const it = ITEMS[t.dataset.c];
  J[it.review_id] = { ...(J[it.review_id] || {}), c: t.value }; save();
});
function cell(v) { v = v == null ? "" : String(v); return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; }
function exportCSV() {
  const left = ITEMS.filter(i => !J[i.review_id]?.v).length;
  if (left && !confirm(`${left} not judged yet. Export anyway?`)) return;
  const lines = ["review_id,model_label,model_intent,model_severity,quote,quote_supports_label,comment,judged_at"];
  for (const it of ITEMS) { const j = J[it.review_id] || {};
    lines.push([it.review_id, it.label, it.intent, it.severity, it.quote, j.v || "", j.c || "", j.at || ""].map(cell).join(",")); }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
  a.download = "golden_quote_check.csv"; a.click();
}
render();
</script></body></html>"""

out = Path(__file__).parent / "quote_check.html"
out.write_text(PAGE.replace("/*__ITEMS__*/[]", json.dumps(items, ensure_ascii=False).replace("</", "<\\/")))
print(f"wrote {out.relative_to(ROOT)} with {len(items)} reviews")
