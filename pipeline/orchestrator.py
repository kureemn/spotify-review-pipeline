"""Orchestrator: code-owned dispatch, state, validation, budgets and logs.

Fixed stage order: ingest -> enrich -> export tables -> verify -> group -> rank -> memo.
Code chooses the next step (stop on spend/time caps or unfinished enrichment); models only
exercise judgement inside their bounded role.
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT, RUNS, RunConfig, prompt_sha
from .llm import FatalError, Ledger, make_provider
from .stages import enrich as enrich_stage
from .stages import export, group, ingest, memo, rank, verify
from .stages.common import Context
from .store import ResultCache, RunStore

STAGES = ("ingest", "enrich", "verify", "group", "rank", "memo")


def code_version():
    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "pipeline"], capture_output=True, text=True).stdout.strip()
    except OSError:
        rev, dirty = "", ""
    h = hashlib.sha256()
    for p in sorted((ROOT / "pipeline").rglob("*")):
        if p.is_file() and p.suffix in {".py", ".md"}:
            h.update(p.read_bytes())
    return {"git_commit": rev or None, "pipeline_dirty": bool(dirty), "pipeline_files_sha256": h.hexdigest()[:16]}


class Logger:
    def __init__(self, path: Path, quiet=False):
        self.path, self.quiet = path, quiet
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, stage, event, **fields):
        row = {"ts": round(time.time(), 3), "stage": stage, "event": event, **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        if not self.quiet and event == "batch" and "progress" in fields:
            pr = fields["progress"]
            note = f" · retrying {fields['failed']}" if fields.get("failed") else ""
            print(f"[{stage}] saved batch {pr['batches']:>5}: +{fields['completed']:<2} -> {pr['completed']:>7,} / {pr['target']:,} "
                  f"completed · ${pr['spent_usd']:.4f}{note}", flush=True)
        elif not self.quiet and event != "batch":
            print(f"[{stage}] {event} " + " ".join(f"{k}={v}" for k, v in fields.items() if k != "review_ids")[:300],
                  flush=True)


def project_spent(exclude_run=None):
    """Spend recorded by every run under runs/ (one shared ledger for the professor's $10 cap)."""
    import sqlite3
    total = 0.0
    for db in RUNS.glob("*/state.db"):
        if db.parent.name == exclude_run:
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            total += con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]
            con.close()
        except sqlite3.Error:
            pass
    return total


def run_pipeline(input_csv: Path, run_id: str, cfg: RunConfig, cache_path: Path, stages=STAGES,
                 allow_config_change=False, quiet=False):
    run_dir = RUNS / run_id
    out = run_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    store = RunStore(run_dir / "state.db")
    saved = store.get("config")
    if saved and saved != cfg.to_dict() and not allow_config_change:
        # Changing model/prompt/schema settings mid-run would mix label configs; refuse loudly.
        changed = {k for k in cfg.to_dict() if saved.get(k) != cfg.to_dict()[k]}
        ignorable = {"workers", "spend_limit_usd", "max_minutes", "stop_after_batches", "mock_faults", "project_budget_usd"}
        if changed - ignorable:
            raise SystemExit(f"Config differs from saved run config in {sorted(changed - ignorable)}; "
                             f"use a new --run-id or --allow-config-change.")
    store.put("config", cfg.to_dict())
    store.put("run_id", run_id)
    store.put("label_config", cfg.label_config())
    log = Logger(out / "run_log.jsonl", quiet)
    t_start = time.time()
    stage_times = store.get("stage_times", {})
    invocation = store.get("invocations", 0) + 1
    store.put("invocations", invocation)
    log("run", "start", run_id=run_id, invocation=invocation, input=str(input_csv), stages=list(stages),
        provider=cfg.provider, label_config=cfg.label_config(), workers=cfg.workers, spend_limit_usd=cfg.spend_limit_usd)

    manifest = {"run_id": run_id, "input": str(input_csv.name), "code": code_version(),
                "label_config": cfg.label_config(), "config": cfg.to_dict(),
                "prompts": {f"{r}_{getattr(cfg, r).prompt_version}": prompt_sha(r, getattr(cfg, r).prompt_version)
                            for r in ("enrich", "verify", "group", "memo")},
                "cache_db": str(cache_path.relative_to(ROOT)) if cache_path.is_relative_to(ROOT) else str(cache_path),
                "python": sys.version.split()[0]}

    def timed(name, fn):
        t0 = time.time()
        result = fn()
        stage_times.setdefault(name, []).append({"invocation": invocation, "seconds": round(time.time() - t0, 3)})
        store.put("stage_times", stage_times)
        return result

    status = "ok"
    ledger = None
    try:
        if "ingest" in stages:
            report = timed("ingest", lambda: ingest.run(store, input_csv, out, log))
            manifest["source"] = {"file_sha256": report["file_sha256"], "bytes": report["bytes"], "records": report["records"]}
        provider = make_provider(cfg) if set(stages) & {"enrich", "verify", "group", "memo"} else None
        cache = ResultCache(cache_path)
        other = project_spent(exclude_run=run_id)
        cap = min(cfg.spend_limit_usd, cfg.project_budget_usd - other)
        ledger = Ledger(cap, already_spent=store.spent_usd())
        log("run", "budget", run_cap_usd=cfg.spend_limit_usd, project_budget_usd=cfg.project_budget_usd,
            other_runs_spent_usd=round(other, 6), this_run_spent_usd=round(store.spent_usd(), 6), effective_cap_usd=round(cap, 6))
        ctx = Context(store, cache, provider, ledger, cfg, run_id, out, log)
        if "enrich" in stages:
            enr = enrich_stage.Enricher(store, cache, provider, ledger, cfg, run_id, log)
            stop = timed("enrich", enr.run)
            if stop != "queue_empty":
                status = f"enrich_stopped:{stop}"
        counts = timed("export", lambda: export.export_tables(store, out))
        pending = store.counts().get("pending", 0)
        downstream = [s for s in stages if s in ("verify", "group", "rank", "memo")]
        if downstream and pending:
            log("run", "downstream_skipped", reason=f"{pending} reviews still pending; resume enrichment first")
            downstream = []
        records = rank.read_jsonl(out / "enriched.jsonl") if downstream else []
        if "verify" in downstream:
            timed("verify", lambda: verify.run(ctx))
        if "group" in downstream:
            timed("group", lambda: group.run(ctx, records))
        if "rank" in downstream:
            ranked, detail, topics = timed("rank", lambda: rank.run_from_files(out))
        if "memo" in downstream:
            facts = run_facts(store, out, counts)
            timed("memo", lambda: memo.run(ctx, ranked, json.loads((out / "issues.json").read_text())["issues"],
                                           topics, records, facts))
    except FatalError as e:
        status = f"fatal:{e}"
        log("run", "fatal", error=str(e))
    finally:
        wall = round(time.time() - t_start, 3)
        walls = store.get("wall_clock", [])
        walls.append({"invocation": invocation, "seconds": wall, "stages": list(stages), "status": status})
        store.put("wall_clock", walls)
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
        write_summary(store, out, cfg, ledger)
        log("run", "end", status=status, wall_clock_s=wall, counts=store.counts(),
            spent_usd=round(store.spent_usd(), 6))
    return status


def run_facts(store, out, counts):
    ing = json.loads((out / "ingestion_report.json").read_text())
    return {"source_records": ing["records"], "nonempty_reviews": ing["nonempty_to_classify"],
            "empty_text_quarantined": ing["empty_review_text"], "completed_classifications": counts["completed"],
            "unresolved_or_quarantined_nonempty": counts["quarantined"] - ing["empty_review_text"],
            "exact_text_reuse_records": counts["cache_reuse"], "review_window": f"{ing['first_review'][:10]} to {ing['last_review'][:10]}",
            "data_caveat": "self-selected public Google Play reviews; no revenue, plan tier or confirmed churn"}


def write_summary(store, out, cfg, ledger):
    con = store.con
    calls = {}
    for role, outcome, n, it, ot, ct, cw, rt, cost, ua in con.execute(
            "SELECT role, outcome, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cached_tokens), SUM(cache_write_tokens), "
            "SUM(reasoning_tokens), SUM(cost_usd), MIN(usage_available) FROM calls GROUP BY role, outcome"):
        calls.setdefault(role, {})[outcome] = {"calls": n, "input_tokens": it, "output_tokens": ot, "cached_tokens": ct,
                                               "cache_write_tokens": cw, "reasoning_tokens": rt,
                                               "cost_usd_estimated_from_usage": round(cost or 0, 6),
                                               "all_usage_available": bool(ua)}
    sessions = [dict(zip(("session", "stage", "started_at", "ended_at", "phase", "completed_before", "completed_after",
                          "calls", "stop_reason"), r)) for r in con.execute("SELECT * FROM sessions ORDER BY session")]
    summary = {"run_id": store.get("run_id"), "label_config": store.get("label_config"), "status_counts": store.counts(),
               "quarantine_reasons": dict(con.execute(
                   "SELECT reason, COUNT(*) FROM reviews WHERE status='quarantined' GROUP BY reason").fetchall()),
               "cache_reuse_records": con.execute("SELECT COUNT(*) FROM results WHERE cache_source_id IS NOT NULL").fetchone()[0],
               "calls": calls, "spent_usd_estimated_from_usage": round(store.spent_usd(), 6),
               "spend_limit_usd": cfg.spend_limit_usd, "project_budget_usd": cfg.project_budget_usd,
               "service_tiers": dict(con.execute("SELECT COALESCE(service_tier,'standard'), COUNT(*) FROM calls GROUP BY 1").fetchall()),
               "ledger_refusals": ledger.refusals if ledger else None,
               "workers": cfg.workers, "stage_times": store.get("stage_times", {}), "wall_clock": store.get("wall_clock", []),
               "sessions": sessions,
               "cost_note": "Costs are usage x rates in pipeline/config.py (see cost/rates.csv); not a provider invoice."}
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
