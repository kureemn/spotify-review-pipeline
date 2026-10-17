"""SQLite state for one run (runs/<run_id>/state.db) and the cross-run exact-text result cache.

All writes happen on the orchestrator thread inside a transaction per batch, so a batch is
either fully saved (records + call log) or not saved at all. Workers only make API calls.
"""

import json
import sqlite3
import time
from pathlib import Path

RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews(
  review_id TEXT PRIMARY KEY, src_order INTEGER NOT NULL,
  review_text TEXT NOT NULL, review_rating TEXT NOT NULL, review_likes TEXT NOT NULL,
  app_version TEXT NOT NULL, review_timestamp TEXT NOT NULL,
  source_sha256 TEXT NOT NULL, text_sha TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', reason TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS reviews_status ON reviews(status);
CREATE INDEX IF NOT EXISTS reviews_text ON reviews(text_sha);
CREATE TABLE IF NOT EXISTS results(
  review_id TEXT PRIMARY KEY REFERENCES reviews(review_id),
  topic TEXT, subtopic TEXT, intent TEXT, sentiment REAL, severity INTEGER,
  entities TEXT, evidence_quote TEXT, needs_review INTEGER, note TEXT,
  label_config TEXT, cache_source_id TEXT, origin_request_id TEXT, origin_run_id TEXT,
  quote_method TEXT, completed_at REAL);
CREATE TABLE IF NOT EXISTS calls(
  request_id TEXT PRIMARY KEY, run_id TEXT, session INTEGER, role TEXT, phase TEXT,
  provider TEXT, model TEXT, effort TEXT, prompt_version TEXT, label_config TEXT,
  outcome TEXT, error TEXT, review_ids TEXT, n_items INTEGER, attempt INTEGER, batch_key TEXT,
  input_tokens INTEGER, cached_tokens INTEGER, cache_write_tokens INTEGER,
  output_tokens INTEGER, reasoning_tokens INTEGER, usage_available INTEGER,
  cost_usd REAL, started_at REAL, ended_at REAL, latency_s REAL, artifacts TEXT, service_tier TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  session INTEGER PRIMARY KEY, stage TEXT, started_at REAL, ended_at REAL, phase TEXT,
  completed_before INTEGER, completed_after INTEGER, calls INTEGER, stop_reason TEXT);
CREATE TABLE IF NOT EXISTS verify(
  review_id TEXT PRIMARY KEY, label TEXT, topic TEXT, subtopic TEXT, intent TEXT,
  severity INTEGER, confidence TEXT, request_id TEXT, verify_config TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS result_cache(
  text_sha TEXT NOT NULL, label_config TEXT NOT NULL,
  origin_review_id TEXT NOT NULL, origin_run_id TEXT NOT NULL, origin_request_id TEXT NOT NULL,
  result TEXT NOT NULL, created_at REAL,
  PRIMARY KEY(text_sha, label_config));
CREATE TABLE IF NOT EXISTS stage_cache(
  key TEXT PRIMARY KEY, role TEXT, response TEXT, request_id TEXT, run_id TEXT, created_at REAL);
"""


def connect(path: Path, schema: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(schema)
    return con


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        self.con = connect(path, RUN_SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(calls)")}
        if "service_tier" not in cols:   # migrate state DBs created before tier tracking
            self.con.execute("ALTER TABLE calls ADD COLUMN service_tier TEXT")

    # -- key/value metadata ---------------------------------------------------------------
    def get(self, key, default=None):
        row = self.con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))

    # -- transactions -----------------------------------------------------------------------
    def tx(self):
        return _Tx(self.con)

    def counts(self):
        return dict(self.con.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status").fetchall())

    def completed_ids(self):
        return [r[0] for r in self.con.execute(
            "SELECT review_id FROM reviews WHERE status='completed' ORDER BY src_order")]

    def log_call(self, call: dict):
        cols = ", ".join(call)
        self.con.execute(f"INSERT INTO calls({cols}) VALUES({', '.join('?' * len(call))})",
                         [json.dumps(v) if isinstance(v, (list, dict)) else v for v in call.values()])

    def spent_usd(self):
        return self.con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]

    def start_session(self, stage, phase, completed_before):
        cur = self.con.execute(
            "INSERT INTO sessions(stage, started_at, phase, completed_before) VALUES(?,?,?,?)",
            (stage, time.time(), phase, completed_before))
        return cur.lastrowid

    def end_session(self, session, completed_after, calls, stop_reason):
        self.con.execute("UPDATE sessions SET ended_at=?, completed_after=?, calls=?, stop_reason=? WHERE session=?",
                         (time.time(), completed_after, calls, stop_reason, session))


class ResultCache:
    """Exact text + label_config -> validated result, with direct origin provenance."""

    def __init__(self, path: Path):
        self.path = path
        self.con = connect(path, CACHE_SCHEMA)

    def get_many(self, text_shas, label_config):
        out = {}
        shas = list(text_shas)
        for i in range(0, len(shas), 500):
            chunk = shas[i:i + 500]
            q = (f"SELECT text_sha, origin_review_id, origin_run_id, origin_request_id, result FROM result_cache "
                 f"WHERE label_config=? AND text_sha IN ({','.join('?' * len(chunk))})")
            for sha, oid, orun, oreq, res in self.con.execute(q, [label_config, *chunk]):
                out[sha] = {"origin_review_id": oid, "origin_run_id": orun, "origin_request_id": oreq,
                            "result": json.loads(res)}
        return out

    # downstream stages (verify/group/memo): response keyed by exact input payload + config
    def stage_get(self, key):
        row = self.con.execute("SELECT response, request_id, run_id FROM stage_cache WHERE key=?", (key,)).fetchone()
        return (json.loads(row[0]), row[1], row[2]) if row else (None, None, None)

    def stage_put(self, key, role, response, request_id, run_id):
        self.con.execute("INSERT OR REPLACE INTO stage_cache VALUES(?,?,?,?,?,?)",
                         (key, role, json.dumps(response, ensure_ascii=False), request_id, run_id, time.time()))

    def put_many(self, rows):
        with _Tx(self.con) as c:
            c.executemany("INSERT OR IGNORE INTO result_cache VALUES(?,?,?,?,?,?,?)", rows)


class _Tx:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, exc, tb):
        self.con.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
