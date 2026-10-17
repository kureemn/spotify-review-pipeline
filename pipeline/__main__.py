"""Command line entry point.

  python -m pipeline run --input data/checkpoint_500.csv --run-id dev500 --provider openai --confirm-paid
  python -m pipeline run --input data/cost_100.csv --run-id mock100 --provider mock      # offline, free
  python -m pipeline pilot --mode cold --confirm-paid                                     # measured 100-review pilot
  python -m pipeline rank --from runs/<id>/out                                            # no model, no key
  python -m pipeline export-grading --run-id <id>
  python -m pipeline status --run-id <id>
"""

import argparse
import json
import sys
import time
from pathlib import Path

from .config import DATA, ROOT, RUNS, RunConfig


def build_cfg(a):
    cfg = RunConfig(provider=a.provider, workers=a.workers, spend_limit_usd=a.spend_limit, max_minutes=a.max_minutes,
                    stop_after_batches=a.stop_after_batches, mock_faults=a.mock_faults)
    if a.verify_rate is not None:
        cfg.verify_rate = a.verify_rate
    if a.batch_size:
        cfg.enrich.batch_size = a.batch_size
    if a.provider == "mock":
        for role in ("enrich", "verify", "group", "memo"):
            getattr(cfg, role).model = "mock-keyword-v1"
    return cfg


def guard_paid(a):
    model_stages = {"enrich", "verify", "group", "memo"} & set(a.stages.split(","))
    if a.provider != "mock" and model_stages and not a.confirm_paid:
        sys.exit("Refusing to make paid API calls without --confirm-paid (use --provider mock for a free offline run).")


def add_run_args(p):
    p.add_argument("--provider", choices=["openai", "mock"], default="mock")
    p.add_argument("--confirm-paid", action="store_true", help="required for any paid provider call")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--spend-limit", type=float, default=2.0, help="USD hard cap for this run (cumulative)")
    p.add_argument("--max-minutes", type=float, default=0)
    p.add_argument("--stop-after-batches", type=int, default=0, help="deliberate interruption for the resume demo")
    p.add_argument("--verify-rate", type=float, default=None)
    p.add_argument("--batch-size", type=int, default=0)
    p.add_argument("--mock-faults", default="")
    p.add_argument("--cache", type=Path, default=ROOT / "cache" / "result_cache.db")
    p.add_argument("--stages", default="ingest,enrich,verify,group,rank,memo")
    p.add_argument("--allow-config-change", action="store_true")
    p.add_argument("--quiet", action="store_true")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="run stages on any input CSV")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    add_run_args(p)
    p = sub.add_parser("pilot", help="the measured 100-review cold/warm pilot on data/cost_100.csv")
    p.add_argument("--mode", choices=["cold", "warm"], required=True)
    p.add_argument("--tag", default="", help="suffix for run ids, e.g. a date")
    add_run_args(p)
    p = sub.add_parser("rank", help="recompute ranking/aggregates from saved files (no model, no key)")
    p.add_argument("--from", dest="src", type=Path, required=True)
    p.add_argument("--records", type=Path, default=None)
    p.add_argument("--membership", type=Path, default=None)
    p = sub.add_parser("export-grading", help="write the standardized grading/ folder for a run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", type=Path, default=ROOT / "grading")
    p.add_argument("--no-gzip", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)

    if a.cmd in ("run", "pilot"):
        from .orchestrator import run_pipeline
        guard_paid(a)
        cfg = build_cfg(a)
        stages = tuple(s for s in a.stages.split(",") if s)
        if a.cmd == "pilot":
            prefix = "pilot" if a.provider != "mock" else "mockpilot"
            run_id = f"{prefix}-{a.mode}{('-' + a.tag) if a.tag else ''}"
            cache = ROOT / "cost" / f"{prefix}_result_cache{('-' + a.tag) if a.tag else ''}.db"
            if a.verify_rate is None:
                cfg.verify_rate = 0.2     # declared pilot verification sample (about 20 of 100)
            cfg.workers = 1
            if a.mode == "cold":
                if (RUNS / run_id).exists() or cache.exists():
                    sys.exit(f"Cold pilot needs an empty result cache and a fresh run dir. Remove {RUNS / run_id} and "
                             f"{cache} (and its -wal/-shm files) first, or pass a new --tag.")
            elif not cache.exists():
                sys.exit("Warm pilot needs the cold pilot's result cache; run --mode cold first.")
            elif (RUNS / run_id).exists():
                sys.exit(f"{RUNS / run_id} exists; pass a new --tag for another warm repeat.")
            status = run_pipeline(DATA / "cost_100.csv", run_id, cfg, cache, stages, quiet=a.quiet)
        else:
            status = run_pipeline(a.input, a.run_id, cfg, a.cache, stages, a.allow_config_change, quiet=a.quiet)
        print(f"status: {status}")
        sys.exit(0 if status == "ok" else 3)

    if a.cmd == "rank":
        from .stages import rank
        t0 = time.time()
        ranked, _, _ = rank.run_from_files(a.src, a.records, a.membership)
        print(f"ranked {len(ranked)} issues from saved files in {time.time() - t0:.2f}s (no model calls)")
        for r in ranked[:10]:
            print(f"{r['rank']:>3} {r['issue_id']:<40} n={r['complaint_count']:>7} sum={r['severity_sum']:>8} "
                  f"mean={r['mean_severity']}")
        return

    if a.cmd == "export-grading":
        from .stages.export import export_grading
        from .store import RunStore
        store = RunStore(RUNS / a.run_id / "state.db")
        export_grading(store, RUNS / a.run_id / "out", a.out, compress=not a.no_gzip)
        print(f"wrote {a.out}")
        return

    if a.cmd == "status":
        from .store import RunStore
        store = RunStore(RUNS / a.run_id / "state.db")
        print(json.dumps({"counts": store.counts(), "spent_usd": round(store.spent_usd(), 6),
                          "label_config": store.get("label_config")}, indent=2))


if __name__ == "__main__":
    main()
