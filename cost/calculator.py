"""100-review cost & runtime calculator (COST_CALCULATOR.md). Standard library only.

Default = OFFLINE REPLAY: recomputes every charge from saved usage (usage.csv) x editable rates
(rates.csv), shows measured cold/warm pilot results and full-run projections. It never imports
an API client and never makes a model call.

    python3 cost/calculator.py                         # offline replay -> cost/report.md + report.json
    python3 cost/calculator.py --rate-multiplier 2     # what-if: all API rates doubled
    python3 cost/calculator.py selftest                # arithmetic invariants (no key, no network)
    python3 cost/calculator.py collect --cold runs/pilot-cold --warm runs/pilot-warm [--dev runs/dev500 ...]
                                                       # rebuild evidence files from saved run state

The PAID pilot is a separate explicit command (see cost/README.md):
    .venv/bin/python -m pipeline pilot --mode cold --provider openai --confirm-paid
"""

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ITEMS = ("input_uncached", "input_cached", "cache_write", "output")
EXPECTED_COST100_SHA = "c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e"


# ----------------------------------------------------------------------------------- collect
def _calls(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM calls ORDER BY started_at")]
    kv = {r[0]: json.loads(r[1]) for r in con.execute("SELECT key, value FROM kv")}
    counts = dict(con.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status").fetchall())
    texts = con.execute("SELECT COUNT(DISTINCT text_sha) FROM reviews WHERE status != 'quarantined' "
                        "OR reason != 'empty_review_text'").fetchone()[0]
    hits = con.execute("SELECT COUNT(*) FROM results WHERE origin_run_id != ?", (kv.get("run_id"),)).fetchone()[0]
    reuse = con.execute("SELECT COUNT(*) FROM results WHERE cache_source_id IS NOT NULL").fetchone()[0]
    sent = con.execute("SELECT COUNT(*) FROM results WHERE origin_run_id = ? AND cache_source_id IS NULL",
                       (kv.get("run_id"),)).fetchone()[0]
    ids = [r[0] for r in con.execute("SELECT review_id FROM reviews ORDER BY src_order")]
    con.close()
    return rows, kv, counts, texts, hits, reuse, sent, ids


def measurement(run_dir: Path, phase: str):
    rows, kv, counts, texts, hits, reuse, sent, ids = _calls(run_dir / "state.db")
    summary = json.loads((run_dir / "out" / "run_summary.json").read_text())
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    cfg = kv["config"]
    stage_times = {k: sum(x["seconds"] for x in v) for k, v in kv.get("stage_times", {}).items()}
    lat = defaultdict(list)
    for r in rows:
        if r["outcome"] == "succeeded":
            lat[r["role"]].append(r["latency_s"])
    return {
        "phase": phase, "run_id": kv["run_id"], "label_config": kv["label_config"],
        "input_file": manifest.get("input"), "input_sha256": manifest.get("source", {}).get("file_sha256"),
        "review_ids": ids, "records": len(ids), "status_counts": counts, "unique_texts": texts,
        "result_cache_hits": hits, "in_run_exact_text_reuse": reuse, "texts_sent_to_model": sent,
        "wall_clock_s": sum(w["seconds"] for w in kv.get("wall_clock", [])), "stage_times_s": stage_times,
        "median_latency_s": {k: round(statistics.median(v), 3) for k, v in lat.items()},
        "stage_settings": {role: {"provider": cfg["provider"], "model": cfg[role]["model"], "effort": cfg[role]["effort"],
                                  "service_tier_requested": cfg[role].get("service_tier", "standard"),
                                  "prompt_version": f"{role}-{cfg[role]['prompt_version']}",
                                  "prompt_sha": manifest["prompts"].get(f"{role}_{cfg[role]['prompt_version']}"),
                                  "batch_size": cfg[role]["batch_size"], "max_output_tokens": cfg[role]["max_output_tokens"]}
                           for role in ("enrich", "verify", "group", "memo")},
        "workers": cfg["workers"], "verify_rate": cfg["verify_rate"], "spend_limit_usd": cfg["spend_limit_usd"],
        "project_budget_usd": cfg.get("project_budget_usd"), "schema": kv["label_config"].split("+")[3],
        "stage_cache_hits": summary.get("stage_cache_hits"),
    }, rows


def collect(a):
    out = a.dir
    calls_out, usage_out, meas = [], [], {"pilot": [], "dev": []}
    for phase, run in (("cold", a.cold), ("warm", a.warm)):
        if run is None:
            continue
        m, rows = measurement(Path(run), phase)
        meas["pilot"].append(m)
        for r in rows:
            c = {k: r[k] for k in ("request_id", "run_id", "role", "phase", "model", "effort", "prompt_version",
                                   "service_tier", "outcome", "error", "attempt", "n_items", "input_tokens", "cached_tokens",
                                   "cache_write_tokens", "output_tokens", "reasoning_tokens", "usage_available",
                                   "latency_s", "started_at", "ended_at")}
            c["pilot_phase"] = phase
            c["service_tier"] = c["service_tier"] or "standard"
            c["review_ids"] = json.loads(r["review_ids"]) if r["review_ids"] else []
            calls_out.append(c)
            usage_out += usage_rows(c)
    for run in a.dev or []:
        m, rows = measurement(Path(run), "dev")
        ok = [r for r in rows if r["role"] == "enrich" and r["outcome"] == "succeeded"]
        if ok:
            m["enrich_stats"] = {"requests_ok": len(ok), "input_tokens_per_request": sum(r["input_tokens"] for r in ok) / len(ok),
                                 "output_tokens_per_review": sum(r["output_tokens"] for r in ok) / max(sum(r["n_items"] for r in ok), 1),
                                 "median_latency_s": m["median_latency_s"].get("enrich"), "workers": m["workers"]}
        m["usage"] = [u for r in rows for u in usage_rows(dict(r, service_tier=r["service_tier"] or "standard", pilot_phase="dev"))]
        m.pop("review_ids")
        meas["dev"].append(m)
    (out / "pilot_calls.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in calls_out))
    with open(out / "usage.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(usage_out[0]) if usage_out else ["request_id"], lineterminator="\n")
        w.writeheader()
        w.writerows(usage_out)
    (out / "measurements.json").write_text(json.dumps(meas, indent=2))
    # pilot_records: one final record per pilot ID from the COLD run (warm status added for comparison)
    cold = Path(a.cold) / "out" / "records.jsonl"
    warm = {json.loads(l)["review_id"]: json.loads(l) for l in open(Path(a.warm) / "out" / "records.jsonl")} if a.warm else {}
    with open(out / "pilot_records.jsonl", "w") as f:
        for line in open(cold):
            rec = json.loads(line)
            wr = warm.get(rec["review_id"], {})
            rec["warm_status"] = wr.get("status")
            rec["warm_labels_identical"] = all(rec.get(k) == wr.get(k) for k in ("topic", "intent", "severity", "evidence_quote")) if wr else None
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(calls_out)} calls, {len(usage_out)} usage rows, measurements for {len(meas['pilot'])} pilot + {len(meas['dev'])} dev runs")


def usage_rows(c):
    """Mutually exclusive billing items per call. cached + cache_write are subtracted from input."""
    inp, cached, cw = c["input_tokens"] or 0, c["cached_tokens"] or 0, c["cache_write_tokens"] or 0
    units = {"input_uncached": max(inp - cached - cw, 0), "input_cached": cached, "cache_write": cw,
             "output": c["output_tokens"] or 0}
    return [{"request_id": c["request_id"], "pilot_phase": c["pilot_phase"], "stage": c["role"], "model": c["model"],
             "tier": c["service_tier"], "outcome": c["outcome"], "item": item, "billed_units": n, "unit": "tokens",
             "usage_available": c["usage_available"], "reasoning_tokens_included_in_output": c["reasoning_tokens"] if item == "output" else ""}
            for item, n in units.items()]


# ------------------------------------------------------------------------------------ replay
def load_rates(path, multiplier=1.0):
    rates = {}
    for r in csv.DictReader(open(path)):
        rates[(r["model"], r["tier"], r["item"])] = {
            "per_unit": float(r["usd_per_unit"]) * multiplier / float(r["unit_size"]), "source": r["source_url"],
            "checked": r["checked_date"], "quoted": f"${float(r['usd_per_unit']) * multiplier:g} per {int(float(r['unit_size'])):,} {r['unit']}"}
    return rates


def price(usage, rates):
    """item_cost = billed_units x price_per_unit; returns per-row costs and unresolved rows."""
    priced, missing = [], []
    for u in usage:
        key = (u["model"], u["tier"], u["item"])
        if key not in rates:
            missing.append(u)
            continue
        priced.append(dict(u, cost_usd=int(u["billed_units"]) * rates[key]["per_unit"]))
    return priced, missing


def stage_table(priced, calls, phase):
    t = defaultdict(lambda: defaultdict(float))
    for u in priced:
        if u["pilot_phase"] == phase:
            s = t[u["stage"]]
            s[u["item"]] += int(u["billed_units"])
            s["cost_usd"] += u["cost_usd"]
    for c in calls:
        if c["pilot_phase"] != phase:
            continue
        s = t[c["role"]]
        s["requests_attempts"] += 1
        s["failed_attempts"] += c["outcome"] == "failed"
        s["retries"] += (c["attempt"] or 1) > 1
        s["reasoning_tokens"] += c["reasoning_tokens"] or 0
        s["items_in_requests"] += c["n_items"] or 0
        s.setdefault("tiers", set()).add(c["service_tier"])
        s["usage_missing"] += not c["usage_available"]
    return t


def project(meas_cold, priced, calls, rates, assumptions, scenario, distinct, label):
    """Extrapolate each stage from its own measured work and throughput; fixed overhead once."""
    sc = assumptions["scenarios"][scenario]
    enr = [c for c in calls if c["pilot_phase"] == "cold" and c["role"] == "enrich"]
    ok = [c for c in enr if c["outcome"] == "succeeded"]
    sent = max(meas_cold["texts_sent_to_model"], 1)
    batch = meas_cold["stage_settings"]["enrich"]["batch_size"]
    model = meas_cold["stage_settings"]["enrich"]["model"]
    tier = sc.get("tier_override") or (ok[0]["service_tier"] if ok else "standard")
    # Per-request input = fixed instructions + per-review text; derive both from measured calls.
    in_tok = sum(c["input_tokens"] for c in ok)
    out_tok = sum(c["output_tokens"] for c in ok)
    items = sum(c["n_items"] for c in ok) or 1
    per_req_in = in_tok / max(len(ok), 1)
    out_per_review = out_tok / items
    basis = "100-review pilot"
    if assumptions.get("_dev_enrich"):          # largest development checkpoint refreshes token rates
        d = assumptions["_dev_enrich"]
        per_req_in, out_per_review, basis = d["input_tokens_per_request"], d["output_tokens_per_review"], d["run_id"]
    # Input billing mix (uncached / cached / cache-write) measured from successful enrichment calls,
    # refreshed by dev checkpoints when available. Conservative: no cache hits, all non-cached input
    # billed at the (more expensive) cache-write rate.
    all_ok = [c for c in calls if c["role"] == "enrich" and c["outcome"] == "succeeded"]
    tot_in = max(sum(c["input_tokens"] for c in all_ok), 1)
    mix = {"input_cached": sum(c["cached_tokens"] for c in all_ok) / tot_in,
           "cache_write": sum(c["cache_write_tokens"] for c in all_ok) / tot_in}
    if assumptions.get("_dev_input_mix"):
        mix = assumptions["_dev_input_mix"]
    if sc["cache_hit_share_of_input"] != "measured":
        mix = {"input_cached": float(sc["cache_hit_share_of_input"]), "cache_write": 1 - float(sc["cache_hit_share_of_input"])}
    mix["input_uncached"] = max(1 - mix["input_cached"] - mix["cache_write"], 0)
    cached_share = mix["input_cached"]
    requests = math.ceil(distinct / batch)
    mult_req = 1 + sc["retry_rate"] + sc["invalid_retry_rate"]
    tokens = {item: requests * per_req_in * share * mult_req for item, share in mix.items()}
    tokens["output"] = distinct * out_per_review * (1 + sc["invalid_retry_rate"] + sc["retry_rate"])
    enrich_cost = sum(tokens[i] * rates[(model, tier, i)]["per_unit"] for i in tokens)
    # Verification: measured cost per verified text x declared full-run rate.
    vcalls = [c for c in calls if c["pilot_phase"] == "cold" and c["role"] == "verify"]
    vcost = sum(u["cost_usd"] for u in priced if u["pilot_phase"] == "cold" and u["stage"] == "verify")
    if tier != (vcalls[0]["service_tier"] if vcalls else tier):
        vcost *= 2 if tier == "standard" else 0.5
    vitems = sum(c["n_items"] for c in vcalls if c["outcome"] == "succeeded") or 1
    verify_cost = assumptions["verify_rate_full_run"] * distinct * (vcost / vitems) * (1 + sc["retry_rate"])
    # Group + memo: fixed overhead once (group scaled to the full label set's number of issues).
    gm = {s: sum(u["cost_usd"] for u in priced if u["pilot_phase"] == "cold" and u["stage"] == s) for s in ("group", "memo")}
    pilot_issues = max(meas_cold.get("pilot_issue_count") or 1, 1)
    full_issues = assumptions.get("expected_full_issue_count", 40)
    fixed = gm["group"] * max(full_issues / pilot_issues, 1) + gm["memo"]
    fallback = assumptions.get("max_fallback_fraction", 0) * distinct * 0   # no stronger-model fallback configured
    total = enrich_cost + verify_cost + fixed + fallback
    # Time: per-request latency at one worker, divided by effective parallelism.
    lat = meas_cold["median_latency_s"].get("enrich") or 0
    workers = assumptions["max_workers"]
    enrich_s = requests * mult_req * lat / (workers * sc["parallel_efficiency"])
    vlat = meas_cold["median_latency_s"].get("verify") or 0
    verify_s = math.ceil(assumptions["verify_rate_full_run"] * distinct / 50) * vlat / (workers * sc["parallel_efficiency"])
    fixed_s = sum(meas_cold["stage_times_s"].get(s, 0) for s in ("group", "rank", "memo")) + assumptions.get("measured_full_ingest_s", 0)
    hours = (enrich_s + verify_s + fixed_s) / 3600
    return {"scenario": scenario, "label": label, "distinct_texts_classified": distinct, "enrich_requests": requests,
            "token_basis": basis, "tier": tier, "cached_input_share": round(cached_share, 4), "cache_write_share": round(mix["cache_write"], 4), "input_tokens_per_request": round(per_req_in, 1),
            "output_tokens_per_review": round(out_per_review, 2), "enrich_usd": enrich_cost, "verify_usd": verify_cost,
            "group_memo_fixed_usd": fixed, "fallback_usd": fallback, "total_api_usd": total,
            "per_1000_inputs_usd": total / assumptions["full_run"]["rows"] * 1000,
            "per_completed_record_usd": total / assumptions["full_run"]["nonempty"],
            "elapsed_hours": hours, "workers": workers,
            "exceeds_budget": total > assumptions["budget_usd"]}


def replay(a):
    calls = [json.loads(l) for l in open(a.dir / "pilot_calls.jsonl")]
    usage = list(csv.DictReader(open(a.dir / "usage.csv")))
    meas = json.loads((a.dir / "measurements.json").read_text())
    assumptions = json.loads(Path(a.assumptions).read_text())
    rates = load_rates(a.rates, a.rate_multiplier)
    priced, missing = price(usage, rates)
    cold = next(m for m in meas["pilot"] if m["phase"] == "cold")
    warm = next((m for m in meas["pilot"] if m["phase"] == "warm"), None)
    issues_file = ROOT / "runs" / cold["run_id"] / "out" / "issues.json"
    if issues_file.exists():
        cold["pilot_issue_count"] = len(json.loads(issues_file.read_text())["issues"])
    full_ingest = ROOT / "runs" / "full-v1" / "out" / "ingestion_report.json"
    if full_ingest.exists():
        assumptions["measured_full_ingest_s"] = json.loads(full_ingest.read_text())["seconds"]
    # Dev checkpoints (500 / 10k) refresh the cache-share estimate when present.
    # The largest dev checkpoint best reflects steady-state prompt caching; use its input mix.
    for d in sorted(meas.get("dev", []), key=lambda d: d["records"]):
        if d.get("enrich_stats"):
            assumptions["_dev_enrich"] = dict(d["enrich_stats"], run_id=d["run_id"])
        ok = [u for u in d.get("usage", []) if u["stage"] == "enrich" and u["outcome"] == "succeeded"]
        tot = sum(int(u["billed_units"]) for u in ok if u["item"] != "output")
        if tot:
            assumptions["_dev_input_mix"] = {i: sum(int(u["billed_units"]) for u in ok if u["item"] == i) / tot
                                             for i in ("input_cached", "cache_write")}

    cost_by_phase = defaultdict(float)
    for u in priced:
        cost_by_phase[u["pilot_phase"]] += u["cost_usd"]
    ids_file = ROOT / "data" / "cost_100.csv"
    ids_ok = None
    if ids_file.exists():
        with open(ids_file, newline="", encoding="utf-8-sig") as f:
            ids_ok = [r["review_id"] for r in csv.DictReader(f)] == cold["review_ids"]
        sha_ok = hashlib.sha256(ids_file.read_bytes()).hexdigest() == EXPECTED_COST100_SHA
    else:
        sha_ok = cold["input_sha256"] == EXPECTED_COST100_SHA
    tables = {p["phase"]: stage_table(priced, calls, p["phase"]) for p in meas["pilot"]}
    distinct = assumptions["full_run"]["distinct_nonempty_texts"]
    projections = [project(cold, priced, calls, rates, assumptions, s, n, lbl)
                   for s in assumptions["scenarios"]
                   for n, lbl in ((distinct, "exact-text reuse"), (assumptions["full_run"]["nonempty"], "no reuse"))]
    completed = cold["status_counts"].get("completed", 0)
    result = {
        "rate_multiplier": a.rate_multiplier, "rates_file": str(a.rates), "unpriced_usage_rows": len(missing),
        "measured": {
            "input": {"file": cold["input_file"], "sha256_matches_manifest": sha_ok, "ids_match_cost_100": ids_ok,
                      "records": cold["records"], "unique_texts": cold["unique_texts"]},
            "cold": {"run_id": cold["run_id"], "status_counts": cold["status_counts"], "wall_clock_s": cold["wall_clock_s"],
                     "stage_times_s": cold["stage_times_s"], "api_usd": cost_by_phase["cold"],
                     "result_cache_hits": cold["result_cache_hits"], "texts_sent_to_model": cold["texts_sent_to_model"],
                     "per_1000_inputs_usd": cost_by_phase["cold"] / cold["records"] * 1000,
                     "per_completed_record_usd": cost_by_phase["cold"] / completed if completed else None,
                     "throughput_records_per_s": cold["records"] / cold["wall_clock_s"] if cold["wall_clock_s"] else None},
            "warm": None if not warm else {
                "run_id": warm["run_id"], "status_counts": warm["status_counts"], "wall_clock_s": warm["wall_clock_s"],
                "api_usd": cost_by_phase["warm"], "result_cache_hits": warm["result_cache_hits"],
                "new_enrichment_calls": sum(1 for c in calls if c["pilot_phase"] == "warm" and c["role"] == "enrich"),
                "new_downstream_calls": sum(1 for c in calls if c["pilot_phase"] == "warm" and c["role"] != "enrich")},
            "stage_settings": cold["stage_settings"], "workers": cold["workers"],
            "stages": {ph: {st: {k: (sorted(v) if isinstance(v, set) else v) for k, v in d.items()} for st, d in t.items()}
                       for ph, t in tables.items()},
        },
        "projections": projections,
        "controls": {"budget_usd": assumptions["budget_usd"], "output_token_cap_per_request": assumptions["output_token_cap_per_request"],
                     "max_workers": assumptions["max_workers"], "max_fallback_fraction": assumptions["max_fallback_fraction"]},
        "local_compute": assumptions["local_compute"],
        "dev_checkpoints": [{k: d[k] for k in ("run_id", "records", "status_counts", "wall_clock_s", "texts_sent_to_model")}
                            | {"api_usd": sum(int(u["billed_units"]) * rates[(u["model"], u["tier"], u["item"])]["per_unit"]
                                              for u in d.get("usage", []) if (u["model"], u["tier"], u["item"]) in rates)}
                            for d in meas.get("dev", [])],
        "rates": {f"{m}|{t}|{i}": v for (m, t, i), v in rates.items() if m != "mock-keyword-v1"},
    }
    if not a.no_write:
        (a.dir / "report.json").write_text(json.dumps(result, indent=2, default=str))
        (a.dir / "report.md").write_text(render(result))
    return result


def money(x):
    if x is None:
        return "unknown"
    return f"${x:.6f}" if x < 0.01 else f"${x:,.4f}" if x < 1 else f"${x:,.2f}"


def render(r):
    m = r["measured"]
    L = ["# Cost & runtime calculator — measured 100-review pilot and full-run projection", "",
         "_Generated by `python3 cost/calculator.py` (offline replay: saved usage × `cost/rates.csv`; no API key, no model calls)._",
         f"_Rate multiplier: {r['rate_multiplier']} · unpriced usage rows: {r['unpriced_usage_rows']}_", "",
         "## Measured: pilot input",
         f"- File `{m['input']['file']}` · SHA-256 matches manifest: **{m['input']['sha256_matches_manifest']}** · "
         f"saved 100 IDs equal `cost_100.csv`: **{m['input']['ids_match_cost_100']}** · records {m['input']['records']} · unique texts {m['input']['unique_texts']}",
         f"- Workers: {m['workers']} (cold pilot runs with one worker and an empty result cache)", "",
         "## Measured: cold vs warm", "", "| | cold | warm |", "|---|---|---|"]
    c, w = m["cold"], m["warm"] or {}
    rows = [("run id", c["run_id"], w.get("run_id")), ("status counts", c["status_counts"], w.get("status_counts")),
            ("end-to-end wall clock (s)", f"{c['wall_clock_s']:.2f}", f"{w.get('wall_clock_s', 0):.2f}" if w else "—"),
            ("API spend", money(c["api_usd"]), money(w.get("api_usd")) if w else "—"),
            ("texts sent to model / result-cache hits", f"{c['texts_sent_to_model']} / {c['result_cache_hits']}",
             f"0 / {w.get('result_cache_hits')}" if w else "—"),
            ("new enrichment calls", "—", w.get("new_enrichment_calls", "—")),
            ("new downstream calls (verify/group/memo)", "—", w.get("new_downstream_calls", "—")),
            ("cost per 1,000 input rows", money(c["per_1000_inputs_usd"]), "—"),
            ("cost per completed record", money(c["per_completed_record_usd"]), "—"),
            ("throughput (records/s)", f"{c['throughput_records_per_s']:.2f}" if c["throughput_records_per_s"] else "—", "—")]
    L += [f"| {a} | {b} | {cc} |" for a, b, cc in rows]
    L += ["", "Stage wall times (cold, s): " + ", ".join(f"{k} {v:.2f}" for k, v in c["stage_times_s"].items()), "",
          "## Measured: per-stage settings and usage (cold)", "",
          "| stage | provider | model | effort | tier used | prompt | batch | attempts | failed | retries | uncached in | cached in | cache write | output (incl. reasoning) | reasoning | cost |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for st, s in m["stages"].get("cold", {}).items():
        cfg = m["stage_settings"][st]
        L.append(f"| {st} | {cfg['provider']} | {cfg['model']} | {cfg['effort']} | {','.join(s.get('tiers', []))} | {cfg['prompt_version']} "
                 f"({cfg['prompt_sha']}) | {cfg['batch_size']} | {int(s['requests_attempts'])} | {int(s['failed_attempts'])} | {int(s['retries'])} | "
                 f"{int(s['input_uncached']):,} | {int(s['input_cached']):,} | {int(s['cache_write']):,} | {int(s['output']):,} | "
                 f"{int(s['reasoning_tokens']):,} | {money(s['cost_usd'])} |")
    L += ["", "Usage marked unavailable is shown as unresolved, never as zero: "
          + str({st: int(s["usage_missing"]) for st, s in m["stages"].get("cold", {}).items()}), "",
          "## Rates used (editable `cost/rates.csv`)", "", "| model / tier / item | quoted | source | checked |", "|---|---|---|---|"]
    L += [f"| {k} | {v['quoted']} | {v['source']} | {v['checked']} |" for k, v in r["rates"].items()]
    L += ["", "Formula: `item_cost = billed_units × price_per_unit` (price per 1M ÷ 1,000,000); items are mutually exclusive "
          "(uncached input = input − cached − cache-write); reasoning tokens are inside output and not added again; "
          "each call is priced at the tier the provider reported using.", "",
          "## Full-run projection (660,622 rows · 660,609 nonempty classifications · 13 empty-text quarantines)", "",
          "| scenario | token basis | work | requests | tier | cached / cache-write share | in tok/req | out tok/review | enrich | verify | group+memo (once) | **total API** | per 1k rows | elapsed (h) | budget |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in r["projections"]:
        L.append(f"| {p['scenario']} | {p['token_basis']} | {p['label']} ({p['distinct_texts_classified']:,}) | {p['enrich_requests']:,} | {p['tier']} | {p['cached_input_share']:.0%} / {p['cache_write_share']:.0%} | "
                 f"{p['input_tokens_per_request']:,.0f} | {p['output_tokens_per_review']:.1f} | {money(p['enrich_usd'])} | {money(p['verify_usd'])} | "
                 f"{money(p['group_memo_fixed_usd'])} | **{money(p['total_api_usd'])}** | {money(p['per_1000_inputs_usd'])} | {p['elapsed_hours']:.1f} | "
                 f"{'⚠️ EXCEEDS' if p['exceeds_budget'] else 'within'} {money(r['controls']['budget_usd'])} |")
    ctl = r["controls"]
    L += ["", f"Controls: spend limit **{money(ctl['budget_usd'])}** (enforced in code across all runs with pre-dispatch reservations) · "
          f"output-token cap {ctl['output_token_cap_per_request']}/request · max workers {ctl['max_workers']} · "
          f"stronger-model fallback fraction {ctl['max_fallback_fraction']:.0%} (none configured).",
          "Base: measured tokens and measured input billing mix (cached / cache-write / uncached), small retry rates. "
          "Conservative: standard tier, zero prompt-cache hits with all input billed at the higher cache-write rate, "
          "10% transient + 5% invalid retries, 50% parallel efficiency.", "",
          f"Local compute: {r['local_compute']['machine']} — cost **unknown** ({r['local_compute']['note']}).", ""]
    if r["dev_checkpoints"]:
        L += ["## Refresh at development checkpoints", "", "| run | records | status | wall clock (s) | texts sent | API spend |", "|---|---|---|---|---|---|"]
        L += [f"| {d['run_id']} | {d['records']} | {d['status_counts']} | {d['wall_clock_s']:.1f} | {d['texts_sent_to_model']} | {money(d['api_usd'])} |"
              for d in r["dev_checkpoints"]]
    return "\n".join(L) + "\n"


def selftest(a):
    base = replay(argparse.Namespace(**{**vars(a), "rate_multiplier": 1.0, "no_write": True}))
    dbl = replay(argparse.Namespace(**{**vars(a), "rate_multiplier": 2.0, "no_write": True}))
    checks = {
        "doubling rates doubles cold API spend": math.isclose(dbl["measured"]["cold"]["api_usd"], 2 * base["measured"]["cold"]["api_usd"], rel_tol=1e-12, abs_tol=1e-15),
        "doubling rates leaves measured time unchanged": dbl["measured"]["cold"]["wall_clock_s"] == base["measured"]["cold"]["wall_clock_s"],
        "doubling rates doubles projected totals": all(math.isclose(x["total_api_usd"], 2 * y["total_api_usd"], rel_tol=1e-9, abs_tol=1e-12)
                                                       for x, y in zip(dbl["projections"], base["projections"])),
        "local compute unaffected by rates": dbl["local_compute"] == base["local_compute"],
    }
    tmp = json.loads(Path(a.assumptions).read_text())
    tmp["full_run"]["distinct_nonempty_texts"] *= 3
    tmp_path = HERE / ".selftest_assumptions.json"
    tmp_path.write_text(json.dumps(tmp))
    try:
        vol = replay(argparse.Namespace(**{**vars(a), "assumptions": tmp_path, "rate_multiplier": 1.0, "no_write": True}))
    finally:
        tmp_path.unlink()
    checks["changing projected volume does not change measured pilot"] = vol["measured"] == base["measured"]
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    sys.exit(0 if all(checks.values()) else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="replay", choices=["replay", "selftest", "collect"])
    ap.add_argument("--dir", type=Path, default=HERE)
    ap.add_argument("--rates", type=Path, default=HERE / "rates.csv")
    ap.add_argument("--assumptions", type=Path, default=HERE / "assumptions.json")
    ap.add_argument("--rate-multiplier", type=float, default=1.0)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--cold", type=Path)
    ap.add_argument("--warm", type=Path)
    ap.add_argument("--dev", type=Path, nargs="*")
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(a)
    elif a.cmd == "selftest":
        selftest(a)
    else:
        r = replay(a)
        if not a.no_write:
            print(f"wrote {a.dir / 'report.md'}")
        for p in r["projections"]:
            print(f"{p['scenario']:<13} {p['label']:<17} total {money(p['total_api_usd'])}  {p['elapsed_hours']:.1f} h  "
                  f"{'EXCEEDS BUDGET' if p['exceeds_budget'] else 'within budget'}")


if __name__ == "__main__":
    main()
