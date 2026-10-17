"""Shared stage context and the cached downstream-call helper (verify / group / memo)."""

from dataclasses import dataclass

from ..config import stable_hash
from ..llm import call_with_retries


@dataclass
class Context:
    store: object
    cache: object
    provider: object
    ledger: object
    cfg: object
    run_id: str
    out: object      # Path to runs/<id>/out
    log: object      # log(stage, event, **fields)
    session: int = 0

    def log_attempts(self, role, attempts, review_ids, label_config=None, phase=None, artifacts=None):
        with self.store.tx():
            for a in attempts:
                self.store.log_call(a | {"run_id": self.run_id, "session": self.session, "role": role, "phase": phase,
                                         "label_config": label_config, "review_ids": review_ids, "n_items": len(review_ids),
                                         "latency_s": round(a["ended_at"] - a["started_at"], 3), "artifacts": artifacts})


def cached_call(ctx, role, rc, instructions, payload, review_ids, config_tag, artifacts=None, validate=None):
    """Return (parsed, source) where source is 'cache', 'model' or an error string.
    Cache key = exact instructions + payload + model/effort/prompt config, so any change re-runs."""
    key = stable_hash([role, config_tag, instructions, payload])
    hit, _, _ = ctx.cache.stage_get(key)
    if hit is not None:
        ctx.log(role, "stage_cache_hit", key=key[:16], review_ids=len(review_ids))
        return hit, "cache"
    feedback = ""
    for retry in range(ctx.cfg.max_invalid_retries + 1):
        parsed, attempts, err = call_with_retries(ctx.provider, ctx.ledger, role=role, rc=rc,
                                                  instructions=instructions + feedback, payload=payload,
                                                  max_transient_retries=ctx.cfg.max_transient_retries, attempt_base=retry)
        problem = validate(parsed) if (parsed is not None and validate) else None
        if problem:
            for a in attempts:
                if a["outcome"] == "succeeded":
                    a["outcome"], a["error"] = "failed", f"validation: {problem}"[:300]
            err = "invalid_output"
            feedback = f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED BY CODE: {problem}. Fix it."
        ctx.log_attempts(role, attempts, review_ids, label_config=config_tag, artifacts=artifacts)
        if parsed is not None and not problem:
            ok = next(a for a in attempts if a["outcome"] == "succeeded")
            ctx.cache.stage_put(key, role, parsed, ok["request_id"], ctx.run_id)
            return parsed, "model"
        if err != "invalid_output":
            return None, err
    return None, "invalid_output_after_retry"
