-- U-Tax event store (design doc "Data model"). Events are the only truth: append-only.
-- Apply once: psql "$DATABASE_URL" -f db/001_events.sql
CREATE TABLE IF NOT EXISTS events (
  id              BIGSERIAL PRIMARY KEY,           -- monotonic; SSE + replay cursor
  ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
  type            TEXT NOT NULL,
  actor           TEXT NOT NULL,                   -- cos|intake|tax|qa|acct|mkt|hr|ceo|system
  case_id         TEXT,                            -- 'case_7' (null for firm-level events)
  provider        TEXT,                            -- local|naive|agentmail|openrouter|...
  status          TEXT NOT NULL,                   -- ok|failed|skipped
  payload         JSONB NOT NULL,                  -- masked display data (what the theater sees)
  tokens_in       INT,
  tokens_out      INT,
  cost_usd        NUMERIC(10,4),
  idempotency_key TEXT UNIQUE,                     -- e.g. 'memo.sent:case_7:v2'
  private         JSONB                            -- gateway-only data (real reply address); never served
);
CREATE INDEX IF NOT EXISTS events_case ON events(case_id, id);
CREATE INDEX IF NOT EXISTS events_type ON events(type, id);

-- Append-only: refuse updates and deletes.
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'events is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS events_no_update ON events;
CREATE TRIGGER events_no_update BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION events_append_only();
