PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    records_discovered INTEGER NOT NULL DEFAULT 0 CHECK (records_discovered >= 0),
    records_processed INTEGER NOT NULL DEFAULT 0 CHECK (records_processed >= 0),
    error_type TEXT,
    error_summary TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_runs_source_started
    ON source_runs (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    official_code TEXT,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    organism TEXT,
    call_type TEXT,
    test_type TEXT,
    group_code TEXT,
    total_places INTEGER CHECK (total_places IS NULL OR total_places >= 0),
    current_stage TEXT,
    detail_url TEXT NOT NULL,
    published_on TEXT,
    application_opens_on TEXT,
    application_closes_on TEXT,
    application_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (application_status IN ('pending', 'open', 'closed', 'unknown')),
    process_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (process_status IN ('in_progress', 'completed', 'suspended', 'cancelled', 'unknown')),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_verified_at TEXT,
    last_content_sha256 TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunities_source_record
    ON opportunities (source_id, source_record_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunities_source_code
    ON opportunities (source_id, official_code)
    WHERE official_code IS NOT NULL AND official_code <> '';
CREATE INDEX IF NOT EXISTS idx_opportunities_application_status
    ON opportunities (application_status, application_closes_on);
CREATE INDEX IF NOT EXISTS idx_opportunities_last_seen
    ON opportunities (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_observations (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_run_id INTEGER REFERENCES source_runs(id) ON DELETE SET NULL,
    observed_at TEXT NOT NULL,
    detail_url TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    UNIQUE (opportunity_id, source_run_id, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_observations_opportunity_time
    ON opportunity_observations (opportunity_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_changes (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_run_id INTEGER REFERENCES source_runs(id) ON DELETE SET NULL,
    changed_at TEXT NOT NULL,
    field_name TEXT NOT NULL,
    previous_value TEXT,
    current_value TEXT,
    evidence_url TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_changes_opportunity_time
    ON opportunity_changes (opportunity_id, changed_at DESC);
