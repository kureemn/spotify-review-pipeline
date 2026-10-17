"""Stage 2 - Classify (ENRICH role).

Code owns: pending queue, exact-text reuse, batching (<=50 reviews/request), ID validation,
quote membership, entity extraction, retry-once policy, quarantine, atomic saves, budgets.
The model owns: reading the language and choosing label/intent/severity/sentiment/quote.
"""

import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from ..config import prompt_text
from ..labels import INTENTS, LABELS, definitions_markdown, extract_entities, sentiment_value, split_label
from ..llm import FatalError, call_with_retries

MAX_BATCH = 50  # hard contract limit, independent of config


def _instructions(cfg):
    return prompt_text("enrich", cfg.enrich.prompt_version).replace("{{DEFINITIONS}}", definitions_markdown())


def _validate_item(item, text, short_limit):
    """Return (record_fields, None) or (None, failure_reason)."""
    if item.get("l") not in LABELS or item.get("t") not in INTENTS:
        return None, "invalid_label"
    sev, sent = item.get("v"), item.get("s")
    if type(sev) is not int or not 1 <= sev <= 5 or type(sent) is not int or not -2 <= sent <= 2:
        return None, "invalid_range"
    note = (item.get("f") or "").strip()
    needs_review = bool(note)
    if len(text) <= short_limit:
        quote, method = text, "full_text_short"
    else:
        q = item.get("q") or ""
        if q.strip() and q in text:
            quote, method = q, "model_exact"
        else:
            pos = text.lower().find(q.lower()) if q.strip() else -1
            if pos < 0:
                return None, "quote_not_substring"
            quote, method = text[pos:pos + len(q)], "model_case_repaired"   # original characters, exact substring
    topic, subtopic = split_label(item["l"])
    return {"topic": topic, "subtopic": subtopic, "intent": item["t"], "severity": sev,
            "sentiment": sentiment_value(sent), "entities": extract_entities(text), "evidence_quote": quote,
            "needs_review": needs_review, "note": note, "quote_method": method}, None


class Enricher:
    def __init__(self, store, cache, provider, ledger, cfg, run_id, log):
        self.store, self.cache, self.provider, self.ledger = store, cache, provider, ledger
        self.cfg, self.run_id, self.log = cfg, run_id, log
        self.label_config = cfg.label_config()
        self.instructions = _instructions(cfg)
        self.stats = {"requests": 0, "attempts": 0, "failed_attempts": 0, "batches_committed": 0,
                      "completed_model": 0, "completed_reuse_in_run": 0, "completed_cache_hit": 0,
                      "quote_fallbacks": 0, "item_retries": 0, "quarantined": 0}

    # ---- bookkeeping ----------------------------------------------------------------------
    def _pending(self):
        rows = self.store.con.execute(
            "SELECT review_id, review_text, text_sha, attempts FROM reviews WHERE status='pending' ORDER BY src_order").fetchall()
        return rows

    def _complete(self, c, review_id, fields, origin_request_id, origin_run_id, cache_source_id=None):
        c.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            review_id, fields["topic"], fields["subtopic"], fields["intent"], fields["sentiment"], fields["severity"],
            json.dumps(fields["entities"]), fields["evidence_quote"], int(fields["needs_review"]), fields["note"],
            self.label_config, cache_source_id, origin_request_id, origin_run_id, fields["quote_method"], time.time()))
        c.execute("UPDATE reviews SET status='completed', reason=NULL WHERE review_id=?", (review_id,))

    def _apply_cache_hits(self, pending):
        """Reviews whose exact text already has a validated result under this label_config."""
        hits = self.cache.get_many({r[2] for r in pending}, self.label_config)
        n = 0
        with self.store.tx() as c:
            for rid, text, tsha, _ in pending:
                h = hits.get(tsha)
                if not h:
                    continue
                own = h["origin_review_id"] == rid
                self._complete(c, rid, h["result"], h["origin_request_id"], h["origin_run_id"],
                               None if own else h["origin_review_id"])
                n += 1
        self.stats["completed_cache_hit"] += n
        return n

    # ---- main loop --------------------------------------------------------------------------
    def run(self):
        cfg = self.cfg
        completed_before = self.store.counts().get("completed", 0)
        prior_sessions = self.store.con.execute(
            "SELECT COUNT(*) FROM calls WHERE role='enrich' AND outcome='succeeded'").fetchone()[0]
        phase = "resume" if prior_sessions else "initial"
        session = self.store.start_session("enrich", phase, completed_before)
        out = self.store.path.parent / "out"
        out.mkdir(exist_ok=True)
        if phase == "resume" and not (out / "checkpoint_before.json").exists():
            (out / "checkpoint_before.json").write_text(json.dumps({"completed_ids": self.store.completed_ids()}))
        self.log("enrich", "session_start", session=session, phase=phase, completed_before=completed_before,
                 label_config=self.label_config, workers=cfg.workers, spend_limit_usd=cfg.spend_limit_usd)

        pending = self._pending()
        self._apply_cache_hits(pending)
        pending = self._pending()
        counts = self.store.counts()
        self.done_total = counts.get("completed", 0)
        self.target = self.done_total + len(pending)   # completed so far + still pending (excludes quarantined)
        # One representative (first in source order) per distinct text; duplicates reuse its result.
        reps, by_text = [], {}
        for rid, text, tsha, attempts in pending:
            if tsha not in by_text:
                by_text[tsha] = []
                reps.append((rid, text, tsha, attempts))
            by_text[tsha].append(rid)
        size = min(cfg.enrich.batch_size, MAX_BATCH)
        queue = [(reps[i:i + size], 0) for i in range(0, len(reps), size)]   # (items, prior invalid retries)
        self.log("enrich", "queued", pending_reviews=len(pending), distinct_texts=len(reps), batches=len(queue))

        stop_reason, deadline = None, (time.time() + cfg.max_minutes * 60) if cfg.max_minutes else None
        consecutive_transient = 0
        inflight = {}
        try:
            with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as pool:
                while queue or inflight:
                    while queue and len(inflight) < max(1, cfg.workers) and not stop_reason:
                        if deadline and time.time() > deadline:
                            stop_reason = "time_limit"
                            break
                        items, retry_n = queue.pop(0)
                        fut = pool.submit(self._request, items, retry_n)
                        inflight[fut] = (items, retry_n)
                    if not inflight:
                        break
                    try:
                        done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    except KeyboardInterrupt:
                        # Stop admitting work, but finish and SAVE requests already sent (they are paid for).
                        if stop_reason == "keyboard_interrupt":
                            raise                       # second Ctrl-C: abort now; saved batches stay saved
                        if not stop_reason:
                            print(f"\n[enrich] Ctrl-C: no new requests; saving {len(inflight)} in-flight batch(es) "
                                  f"before exit (press again to abort without saving)", flush=True)
                        stop_reason = stop_reason or "keyboard_interrupt"
                        continue
                    for fut in done:
                        items, retry_n = inflight.pop(fut)
                        try:
                            parsed, attempts, err = fut.result()
                        except FatalError as e:
                            stop_reason = stop_reason or f"fatal: {e}"
                            # record the failed attempt(s) so the call log stays complete; reviews stay pending
                            self._commit(items, retry_n, None, getattr(e, "attempts", []), "fatal", by_text, session, phase)
                            continue
                        follow_ups = self._commit(items, retry_n, parsed, attempts, err, by_text, session, phase)
                        if err == "spend_limit":
                            stop_reason = stop_reason or "spend_limit"
                        if err == "transient_exhausted":
                            consecutive_transient += 1
                            if consecutive_transient >= 3:
                                stop_reason = stop_reason or "repeated_transient_failures"
                        else:
                            consecutive_transient = 0
                        for fu in reversed(follow_ups):
                            queue.insert(0, fu)
                        if cfg.stop_after_batches and self.stats["batches_committed"] >= cfg.stop_after_batches:
                            stop_reason = stop_reason or "deliberate_interrupt"
                    if stop_reason and not inflight:
                        break
        except KeyboardInterrupt:
            stop_reason = "keyboard_interrupt"
        except FatalError as e:
            stop_reason = f"fatal: {e}"
        completed_after = self.store.counts().get("completed", 0)
        stop_reason = stop_reason or "queue_empty"
        self.store.end_session(session, completed_after, self.stats["attempts"], stop_reason)
        if phase == "resume":
            (out / "checkpoint_after.json").write_text(json.dumps({"completed_ids": self.store.completed_ids()}))
        self.log("enrich", "session_end", session=session, phase=phase, stop_reason=stop_reason,
                 completed_before=completed_before, completed_after=completed_after, spent_usd=round(self.ledger.spent, 6),
                 **self.stats)
        return stop_reason

    def _request(self, items, retry_n):
        payload = json.dumps([{"i": k + 1, "short": len(t) <= self.cfg.short_quote_chars, "text": t}
                              for k, (_, t, _, _) in enumerate(items)], ensure_ascii=False)
        return call_with_retries(self.provider, self.ledger, role="enrich", rc=self.cfg.enrich,
                                 instructions=self.instructions, payload=payload,
                                 max_transient_retries=self.cfg.max_transient_retries, attempt_base=retry_n)

    def _commit(self, items, retry_n, parsed, attempts, err, by_text, session, phase):
        """Validate one response and save everything about it in ONE transaction."""
        ids = [rid for rid, _, _, _ in items]
        failed_items, follow_ups = [], []
        completed, quarantined, cache_rows = [], [], []
        if parsed is not None:
            seen = {}
            for it in parsed.get("items", []) if isinstance(parsed, dict) else []:
                k = it.get("i") if isinstance(it, dict) else None
                if isinstance(k, int) and 1 <= k <= len(items) and k not in seen:
                    seen[k] = it          # unknown or duplicated indices are ignored, never guessed
            for k, (rid, text, tsha, att) in enumerate(items, 1):
                it = seen.get(k)
                if it is None:
                    failed_items.append(((rid, text, tsha, att), "model_omitted_item"))
                    continue
                fields, why = _validate_item(it, text, self.cfg.short_quote_chars)
                if why:
                    failed_items.append(((rid, text, tsha, att), why))
                else:
                    completed.append((rid, tsha, fields))
        elif err == "invalid_output":
            failed_items = [(x, "invalid_output") for x in items]
        elif err == "content_rejected":
            # The provider refused the whole prompt (safety filter on some review). Not an invalid answer:
            # bisect without spending the invalid-output retry, until the offending review stands alone.
            if len(items) > 1:
                mid = len(items) // 2
                follow_ups = [(items[:mid], retry_n), (items[mid:], retry_n)]
                self.stats["content_splits"] = self.stats.get("content_splits", 0) + 1
            else:
                quarantined.append((items[0][0], "provider_content_filter_rejected"))
        # transient_exhausted / spend_limit / fatal: items stay pending for a later resume (not failures of the review).

        if failed_items:
            if retry_n < self.cfg.max_invalid_retries:
                self.stats["item_retries"] += len(failed_items)
                retry = [x for x, _ in failed_items]
                if len(retry) == len(items) and len(retry) > 1:
                    # Whole-batch failure (truncated or malformed output): the single retry is sent as two halves.
                    mid = len(retry) // 2
                    follow_ups = [(retry[:mid], retry_n + 1), (retry[mid:], retry_n + 1)]
                else:
                    follow_ups = [(retry, retry_n + 1)]
            else:
                for (rid, text, tsha, att), why in failed_items:
                    if why == "quote_not_substring":
                        # Saved with a visible flag instead of being lost: the full text is always an exact substring.
                        it = seen.get(items.index((rid, text, tsha, att)) + 1)
                        fields, _ = _validate_item(dict(it, q=""), text, len(text))
                        fields.update(needs_review=True, note="quote_fallback_full_text", quote_method="fallback_full_text")
                        completed.append((rid, tsha, fields))
                        self.stats["quote_fallbacks"] += 1
                    else:
                        quarantined.append((rid, f"{why}_after_retry"))

        ok_attempt = next((a for a in attempts if a["outcome"] == "succeeded"), None)
        with self.store.tx() as c:
            for a in attempts:
                self.store.log_call(a | {"run_id": self.run_id, "session": session, "role": "enrich", "phase": phase,
                                         "label_config": self.label_config, "review_ids": ids, "n_items": len(ids),
                                         "batch_key": ids[0], "latency_s": round(a["ended_at"] - a["started_at"], 3)})
            c.execute(f"UPDATE reviews SET attempts=attempts+? WHERE review_id IN ({','.join('?' * len(ids))})",
                      [len(attempts), *ids])
            for rid, tsha, fields in completed:
                self._complete(c, rid, fields, ok_attempt["request_id"], self.run_id)
                self.stats["completed_model"] += 1
                self.done_total += 1
                for dup in by_text.get(tsha, [])[1:]:
                    self._complete(c, dup, fields, ok_attempt["request_id"], self.run_id, cache_source_id=rid)
                    self.stats["completed_reuse_in_run"] += 1
                    self.done_total += 1
                cache_rows.append((tsha, self.label_config, rid, self.run_id, ok_attempt["request_id"],
                                   json.dumps(fields, ensure_ascii=False), time.time()))
            for rid, reason in quarantined:
                tsha = next(t for r, _, t, _ in items if r == rid)
                for member in by_text.get(tsha, [rid]):
                    c.execute("UPDATE reviews SET status='quarantined', reason=? WHERE review_id=?", (reason, member))
                    self.stats["quarantined"] += 1
        if cache_rows:
            self.cache.put_many(cache_rows)
        self.stats["requests"] += 1
        self.stats["attempts"] += len(attempts)
        self.stats["failed_attempts"] += sum(a["outcome"] == "failed" for a in attempts)
        if parsed is not None or err in ("invalid_output", "content_rejected"):
            self.stats["batches_committed"] += 1
        self.log("enrich", "batch", n=len(ids), completed=len(completed), failed=len(failed_items),
                 failure_reasons={r: sum(1 for _, w in failed_items if w == r) for r in {w for _, w in failed_items}},
                 failed_ids=[x[0] for x, _ in failed_items][:50],
                 quarantined=len(quarantined), retry_round=retry_n, error=err, attempts=len(attempts),
                 progress={"completed": self.done_total, "target": self.target, "spent_usd": round(self.ledger.spent, 4),
                           "batches": self.stats["batches_committed"]})
        return follow_ups
