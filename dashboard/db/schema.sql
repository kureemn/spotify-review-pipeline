-- Processed review data, aggregates and analysis outputs served by the dashboard backend.
-- Loaded from saved pipeline outputs by dashboard/db/load.py (no model calls). Re-runnable: drops and recreates.

DROP TABLE IF EXISTS reviews, issues, topic_aggregates, monthly, claims, memo, metrics, runs CASCADE;

CREATE TABLE runs (
  run_id        text PRIMARY KEY,
  label_config  text NOT NULL,
  source_sha256 text NOT NULL,
  loaded_at     timestamptz NOT NULL DEFAULT now(),
  manifest      jsonb NOT NULL
);

-- One row per source review ID (completed or quarantined). Review text is not copied wholesale;
-- evidence_quote is an exact substring of the original text (the whole text for short reviews).
CREATE TABLE reviews (
  review_id        text PRIMARY KEY,
  src_order        integer NOT NULL,
  review_rating    smallint,
  review_timestamp timestamp,
  app_version      text,
  status           text NOT NULL,          -- completed | quarantined
  reason           text,                   -- quarantine reason
  topic            text,
  subtopic         text,
  intent           text,
  sentiment        real,
  severity         smallint,
  needs_review     boolean,
  review_note      text,
  evidence_quote   text,
  entities         text[],
  cache_source_id  text,
  label_config     text,
  issue_id         text                    -- membership (complaint/cancellation only)
);
CREATE INDEX reviews_issue ON reviews(issue_id, severity DESC);
CREATE INDEX reviews_topic ON reviews(topic, intent);

CREATE TABLE issues (
  issue_id          text PRIMARY KEY,
  rank              integer NOT NULL,
  topic             text NOT NULL,
  subtopic          text NOT NULL,
  name              text,
  summary           text,
  coherence         text,
  complaint_count   integer NOT NULL,
  severity_sum      integer NOT NULL,
  mean_severity     text NOT NULL,        -- six-decimal half-up string, exactly as ranking.csv
  priority_score    integer NOT NULL,
  cancellation_count integer NOT NULL,
  needs_review_count integer NOT NULL,
  sev               integer[] NOT NULL,   -- counts of severity 1..5
  example_ids       text[] NOT NULL
);

CREATE TABLE topic_aggregates (
  area                    text PRIMARY KEY,
  completed_records       integer, complaint_records integer, cancellation_records integer,
  severity_sum            integer, mean_severity text, sev4plus_complaints integer,
  share_of_all_complaints text, share_of_completed text
);

-- Complaint share by calendar month and area (first/last months are partial; denominators kept).
CREATE TABLE monthly (
  month text, area text, completed integer, complaints integer,
  PRIMARY KEY (month, area)
);

CREATE TABLE claims (claim_id text PRIMARY KEY, issue_id text, metric text, value text);

CREATE TABLE memo (
  run_id text PRIMARY KEY, priority_area text, headline text, memo_markdown text,
  status text, validation_problems jsonb, cited_claims text[], model text
);

-- Overall metrics (counts, cost, verification, evaluation) as typed JSON values.
CREATE TABLE metrics (key text PRIMARY KEY, value jsonb NOT NULL);
