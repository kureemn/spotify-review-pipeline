"""Run configuration. Every value that changes model behaviour feeds label_config / cache keys."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .labels import ENTITY_VERSION, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = Path(__file__).resolve().parent / "prompts"
DATA = ROOT / "data"
RUNS = ROOT / "runs"
CHECKER = DATA / "check_submission.py"

# Model allowlist: anything else is refused before a call is made.
MODEL_ALLOWLIST = {"gpt-6-luna", "mock-keyword-v1"}

# USD per 1M tokens, checked 2026-10-16 at https://developers.openai.com/api/docs/models/gpt-6-luna .
# The page lists standard rates and states Batch and Flex cost 50% of standard; flex rows apply that.
# Editable copy lives in cost/rates.csv (the calculator reads that file, not these defaults).
DEFAULT_RATES = {
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50},
    "gpt-6-luna@flex": {"input": 0.05, "cached_input": 0.005, "cache_write": 0.0625, "output": 0.25},
    "mock-keyword-v1": {"input": 0.0, "cached_input": 0.0, "cache_write": 0.0, "output": 0.0},
}


@dataclass
class RoleConfig:
    model: str = "gpt-6-luna"
    effort: str = "none"
    prompt_version: str = "v1"
    batch_size: int = 50
    max_output_tokens: int = 4000
    service_tier: str = "standard"      # "flex" = 50% price, slower, more 429s (retried with backoff)


@dataclass
class RunConfig:
    provider: str = "openai"            # "openai" or "mock" (offline plumbing tests, never business results)
    enrich: RoleConfig = field(default_factory=lambda: RoleConfig(batch_size=50, max_output_tokens=3500, service_tier="flex"))
    verify: RoleConfig = field(default_factory=lambda: RoleConfig(batch_size=50, max_output_tokens=3000, service_tier="flex"))
    group: RoleConfig = field(default_factory=lambda: RoleConfig(batch_size=60, max_output_tokens=6000))
    memo: RoleConfig = field(default_factory=lambda: RoleConfig(batch_size=1, max_output_tokens=3000))
    short_quote_chars: int = 150        # reviews this short use the full text as evidence_quote
    verify_rate: float = 0.02           # declared deterministic verification sample fraction
    verify_seed: str = "verify-v1"
    group_examples: int = 8             # bounded examples per issue for the grouping role
    memo_top_issues: int = 12
    memo_evidence_per_issue: int = 3
    workers: int = 1
    spend_limit_usd: float = 5.0        # hard cap for THIS run, enforced with reservations
    max_minutes: float = 0              # 0 = no time cap
    max_transient_retries: int = 4      # 429/5xx/timeouts, backoff + jitter
    max_invalid_retries: int = 1        # invalid output: retry once, then save-with-flag or quarantine
    request_timeout_s: float = 300      # flex requests can queue; long timeout avoids paying for retries
    project_budget_usd: float = 10.0    # professor's cap: total across ALL runs in runs/, enforced at dispatch
    stop_after_batches: int = 0         # >0: deliberate interruption for the resume demo
    mock_faults: str = ""               # e.g. "transient=7,malformed=5,drop=3" (mock provider only)

    def label_config(self) -> str:
        e = self.enrich
        return (f"{e.model}+effort-{e.effort}+enrich-{e.prompt_version}+{SCHEMA_VERSION}"
                f"+quote{self.short_quote_chars}+{ENTITY_VERSION}")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        for role in ("enrich", "verify", "group", "memo"):
            if role in d and isinstance(d[role], dict):
                d[role] = RoleConfig(**d[role])
        return cls(**d)


def prompt_text(role: str, version: str) -> str:
    return (PROMPTS / f"{role}_{version}.md").read_text(encoding="utf-8")


def prompt_sha(role: str, version: str) -> str:
    return hashlib.sha256(prompt_text(role, version).encode()).hexdigest()[:12]


def stable_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
