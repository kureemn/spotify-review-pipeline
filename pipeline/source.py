"""Source reading and hashing. Uses the course's own check_submission helpers so row hashes
and the ingestion profile are byte-for-byte what the grader computes."""

import hashlib
import importlib.util

from .config import CHECKER

_spec = importlib.util.spec_from_file_location("course_checker", CHECKER)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

FIELDS = checker.FIELDS
row_sha = checker.row_sha          # SHA-256 of canonical JSON of the six original fields
csv_rows = checker.csv_rows        # strict multiline-safe CSV reader
profile = checker.profile          # deterministic full-file profile -> grading/ingestion.json
file_sha = checker.sha
mean_string = checker.mean_string  # Decimal half-up, six places


def text_sha(text: str) -> str:
    """Exact-text cache key: no normalisation, so only byte-identical texts share results."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
