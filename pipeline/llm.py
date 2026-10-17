"""Model access: providers, structured-output schemas, bounded retries and the spend ledger.

Every attempt (succeeded or failed) becomes one call-log row. Cost is computed from actual
usage x editable rates; nothing here decides labels or counts.
"""

import json
import os
import random
import re
import threading
import time
import uuid

from .config import DEFAULT_RATES, MODEL_ALLOWLIST
from .labels import INTENTS, LABELS


class TransientError(Exception):
    """Rate limit, timeout, connection or 5xx: retried with backoff."""


class FatalError(Exception):
    """Auth/permission/bad request: stop the run."""


class InvalidOutput(Exception):
    """Response arrived but is not usable (bad JSON, incomplete, refused)."""


def _strict(props: dict, required=None) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": required or list(props)}


SCHEMAS = {
    # Compact keys keep output tokens (the dominant cost) low: i=index, l=label, t=intent,
    # v=severity, s=sentiment, q=quote, f=needs-review reason ("" = no review needed).
    "enrich": _strict({"items": {"type": "array", "items": _strict({
        "i": {"type": "integer"},
        "l": {"type": "string", "enum": list(LABELS)},
        "t": {"type": "string", "enum": list(INTENTS)},
        "v": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "s": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
        "q": {"type": "string"},
        "f": {"type": "string"},
    })}}),
    "verify": _strict({"items": {"type": "array", "items": _strict({
        "i": {"type": "integer"},
        "label": {"type": "string", "enum": list(LABELS)},
        "intent": {"type": "string", "enum": list(INTENTS)},
        "severity": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    })}}),
    "group": _strict({"issues": {"type": "array", "items": _strict({
        "issue_id": {"type": "string"},
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "coherence": {"type": "string", "enum": ["coherent", "mixed", "mislabeled"]},
        "outlier_ids": {"type": "array", "items": {"type": "string"}},
    })}}),
    "memo": _strict({
        "priority_area": {"type": "string", "enum": ["access", "usability", "playback", "billing_support"]},
        "headline": {"type": "string"},
        "memo_markdown": {"type": "string"},
    }),
}


def cost_usd(model: str, usage: dict, rates: dict = None, tier: str = "standard") -> float:
    """Mutually exclusive billing items: uncached input, cached input, cache writes, output.
    Reasoning tokens are already inside output_tokens, so they are not added again.
    Rates are looked up for the processing tier actually used (standard or flex)."""
    table = rates or DEFAULT_RATES
    r = table.get(f"{model}@{tier}") or table[model]
    inp = usage.get("input_tokens") or 0
    cached = usage.get("cached_tokens") or 0
    cw = usage.get("cache_write_tokens") or 0
    uncached = max(inp - cached - cw, 0)
    return (uncached * r["input"] + cached * r["cached_input"] + cw * r["cache_write"]
            + (usage.get("output_tokens") or 0) * r["output"]) / 1_000_000


class Ledger:
    """One spend ledger shared by all workers. Reserve worst-case cost before dispatch;
    refuse new work when spent + reserved + next reservation would exceed the cap."""

    def __init__(self, cap_usd: float, already_spent: float = 0.0):
        self.cap, self.spent, self.reserved = cap_usd, already_spent, 0.0
        self.lock = threading.Lock()
        self.refusals = 0

    def reserve(self, amount: float) -> bool:
        with self.lock:
            if self.spent + self.reserved + amount > self.cap:
                self.refusals += 1
                return False
            self.reserved += amount
            return True

    def settle(self, reserved: float, actual: float):
        with self.lock:
            self.reserved -= reserved
            self.spent += actual


def worst_case_cost(model: str, input_chars: int, max_output_tokens: int) -> float:
    # ~3 chars/token is pessimistic for this corpus (emoji, non-Latin scripts); assume no cache hits
    # and standard-tier prices even when flex is requested (it may fall back).
    return cost_usd(model, {"input_tokens": input_chars // 3 + 200, "output_tokens": max_output_tokens})


# ---- providers ------------------------------------------------------------------------------

class OpenAIProvider:
    name = "openai"

    def __init__(self, timeout_s: float):
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv()
        if not os.environ.get("OPENAI_API_KEY"):
            raise FatalError("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.")
        # max_retries=0: our own loop retries so that every attempt is logged.
        self.client = OpenAI(max_retries=0, timeout=timeout_s)
        import openai
        self.openai = openai

    def complete(self, role, instructions, payload, model, effort, max_output_tokens, service_tier="standard"):
        o = self.openai
        try:
            extra = {"service_tier": "flex"} if service_tier == "flex" else {}
            resp = self.client.responses.create(
                model=model, instructions=instructions, input=payload, **extra,
                reasoning={"effort": effort}, max_output_tokens=max_output_tokens, store=False,
                prompt_cache_key=f"spotify-a5-{role}",
                text={"format": {"type": "json_schema", "name": f"{role}_output",
                                 "schema": SCHEMAS[role], "strict": True}})
        except (o.RateLimitError, o.APITimeoutError, o.APIConnectionError, o.InternalServerError) as e:
            raise TransientError(f"{type(e).__name__}: {str(e)[:200]}")
        except o.APIStatusError as e:
            if e.status_code in (408, 409, 429) or e.status_code >= 500:
                raise TransientError(f"HTTP {e.status_code}: {str(e)[:200]}")
            raise FatalError(f"HTTP {e.status_code}: {str(e)[:300]}")
        u = resp.usage
        usage = {"input_tokens": u.input_tokens if u else None, "output_tokens": u.output_tokens if u else None,
                 "cached_tokens": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0,
                 "cache_write_tokens": getattr(getattr(u, "input_tokens_details", None), "cache_write_tokens", 0) or 0,
                 "reasoning_tokens": getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0,
                 "usage_available": u is not None,
                 # tier the provider reports it actually used ("flex", "default", ...)
                 "service_tier": "flex" if getattr(resp, "service_tier", None) == "flex" else "standard"}
        request_id = getattr(resp, "_request_id", None) or resp.id
        if resp.status != "completed":
            reason = getattr(getattr(resp, "incomplete_details", None), "reason", resp.status)
            raise InvalidOutput(f"incomplete:{reason}", usage, request_id)
        return resp.output_text, usage, request_id


class MockProvider:
    """Deterministic keyword labeller for offline plumbing tests (zero cost, never business results).
    mock_faults like "transient=7,malformed=5,drop=3,badquote=4" injects failures every Nth call."""
    name = "mock"

    def __init__(self, faults: str = ""):
        self.faults = {k: int(v) for k, v in (p.split("=") for p in faults.split(",") if p)}
        self.n = 0
        self.lock = threading.Lock()

    def _tick(self):
        with self.lock:
            self.n += 1
            return self.n

    def complete(self, role, instructions, payload, model, effort, max_output_tokens, service_tier="standard"):
        n = self._tick()
        time.sleep(0.01)
        usage = {"input_tokens": (len(instructions) + len(payload)) // 4, "cached_tokens": 0, "cache_write_tokens": 0,
                 "reasoning_tokens": 0, "usage_available": True, "service_tier": service_tier}
        rid = f"mock-{uuid.uuid4().hex[:12]}"
        if self.faults.get("transient") and n % self.faults["transient"] == 0:
            raise TransientError("mock 429 rate limit")
        data = json.loads(payload)
        if role == "enrich":
            items = [self._enrich_item(x) for x in data]
            if self.faults.get("drop") and n % self.faults["drop"] == 0 and len(items) > 1:
                items = items[:-1]
            if self.faults.get("badquote") and n % self.faults["badquote"] == 0:
                for it, x in zip(items, data):
                    if not x["short"]:
                        it["q"] = "THIS QUOTE IS NOT IN THE REVIEW"
                        break
            out = {"items": items}
        elif role == "verify":
            out = {"items": [{"i": it["i"], "label": it["l"], "intent": it["t"], "severity": it["v"], "confidence": "medium"}
                             for it in (self._enrich_item(x) for x in data)]}
        elif role == "group":
            out = {"issues": [{"issue_id": i["issue_id"], "name": i["issue_id"].replace(".", ": ").replace("_", " "),
                               "summary": "Mock summary of " + i["issue_id"], "coherence": "coherent", "outlier_ids": []}
                              for i in data["issues"]]}
        else:
            claims = data.get("CLAIMS", [])[:2]
            cited = " ".join(f"{c['issue_id']} {c['metric']} is {c['value']} [{c['claim_id']}]." for c in claims)
            out = {"priority_area": "playback", "headline": "Mock memo for offline plumbing test",
                   "memo_markdown": f"## Recommendation\nMOCK OUTPUT. {cited}\n## Supporting evidence\n-\n"
                                    f"## Alternatives considered\n-\n## Limitations\nMock provider."}
        text = json.dumps(out)
        if self.faults.get("malformed") and n % self.faults["malformed"] == 0:
            text = text[: len(text) // 2]
        usage["output_tokens"] = len(text) // 4
        return text, usage, rid

    @staticmethod
    def _enrich_item(x):
        t = x["text"].lower()
        rules = [("crash", "playback.crashes_wont_open", "complaint", 4), ("offline", "downloads.offline_mode", "complaint", 3),
                 ("ads", "usability.ads", "complaint", 2), ("premium", "billing.free_tier_restrictions", "complaint", 3),
                 ("login", "access.login_failure", "complaint", 4), ("uninstall", "other.general", "cancellation", 2)]
        label, intent, sev = "other.general", "praise", 1
        for kw, lab, it, s in rules:
            if kw in t:
                label, intent, sev = lab, it, s
                break
        else:
            if re.search(r"\b(bad|worst|hate|terrible)\b", t):
                intent, sev = "complaint", 2
        words = x["text"].split()
        quote = "" if x.get("short", True) else " ".join(words[: min(8, len(words))])
        return {"i": x["i"], "l": label, "t": intent, "v": sev,
                "s": -1 if intent in ("complaint", "cancellation") else 1, "q": quote, "f": ""}


def make_provider(cfg):
    if cfg.provider == "mock":
        return MockProvider(cfg.mock_faults)
    if cfg.provider == "openai":
        return OpenAIProvider(cfg.request_timeout_s)
    raise FatalError(f"unknown provider {cfg.provider}")


def call_with_retries(provider, ledger, *, role, rc, instructions, payload, max_transient_retries, attempt_base=0):
    """Run one logical request. Transient failures retry with exponential backoff + jitter.
    Returns (parsed_json_or_None, attempts, error) where attempts are call-log dicts (without run fields)."""
    model = rc.model if provider.name != "mock" else "mock-keyword-v1"
    if model not in MODEL_ALLOWLIST:
        raise FatalError(f"model {model} not in allowlist")
    attempts = []
    for k in range(max_transient_retries + 1):
        reserve = worst_case_cost(model, len(instructions) + len(payload), rc.max_output_tokens)
        if not ledger.reserve(reserve):
            return None, attempts, "spend_limit"
        t0 = time.time()
        rec = {"model": model, "effort": rc.effort, "prompt_version": rc.prompt_version, "provider": provider.name,
               "service_tier": rc.service_tier,
               "attempt": attempt_base + k + 1, "started_at": t0}
        try:
            text, usage, rid = provider.complete(role, instructions, payload, model, rc.effort, rc.max_output_tokens,
                                                 rc.service_tier)
            actual = cost_usd(model, usage, tier=usage.get("service_tier", "standard"))
            ledger.settle(reserve, actual)
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError) as e:
                attempts.append(rec | _usage_fields(usage) | {"request_id": rid, "outcome": "failed",
                                                              "error": f"malformed_json: {e}", "cost_usd": actual,
                                                              "ended_at": time.time()})
                return None, attempts, "invalid_output"
            attempts.append(rec | _usage_fields(usage) | {"request_id": rid, "outcome": "succeeded", "error": None,
                                                          "cost_usd": actual, "ended_at": time.time()})
            return parsed, attempts, None
        except InvalidOutput as e:
            usage, rid = (e.args[1], e.args[2]) if len(e.args) == 3 else ({}, f"local-{uuid.uuid4().hex[:12]}")
            actual = cost_usd(model, usage, tier=usage.get("service_tier", "standard")) if usage else 0.0
            ledger.settle(reserve, actual)
            attempts.append(rec | _usage_fields(usage) | {"request_id": rid, "outcome": "failed", "error": str(e.args[0]),
                                                          "cost_usd": actual, "ended_at": time.time()})
            return None, attempts, "invalid_output"
        except TransientError as e:
            # Unknown whether a timed-out request was billed: log zero with usage_available=0 to reconcile later.
            ledger.settle(reserve, 0.0)
            attempts.append(rec | _usage_fields({}) | {"request_id": f"local-{uuid.uuid4().hex[:12]}", "outcome": "failed",
                                                       "error": str(e), "cost_usd": 0.0, "ended_at": time.time()})
            if k < max_transient_retries:
                time.sleep(min(30, (2 ** k)) * (0.5 + random.random()))
        except FatalError:
            ledger.settle(reserve, 0.0)
            raise
    return None, attempts, "transient_exhausted"


def _usage_fields(usage):
    return {"input_tokens": usage.get("input_tokens") or 0, "output_tokens": usage.get("output_tokens") or 0,
            "cached_tokens": usage.get("cached_tokens") or 0, "cache_write_tokens": usage.get("cache_write_tokens") or 0,
            "reasoning_tokens": usage.get("reasoning_tokens") or 0, "usage_available": int(bool(usage.get("usage_available"))),
            **({"service_tier": usage["service_tier"]} if usage.get("service_tier") else {})}
