PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS models (
    version TEXT PRIMARY KEY, created_at TEXT NOT NULL,
    dataset_version TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
    config_sha256 TEXT NOT NULL, prompt_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY, model_version TEXT NOT NULL REFERENCES models(version),
    deployed_at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS deployments_time ON deployments(deployed_at);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
    route TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('realtime','batch')),
    model_version TEXT REFERENCES models(version),
    status_code INTEGER NOT NULL, latency_ms REAL NOT NULL CHECK(latency_ms >= 0),
    item_count INTEGER NOT NULL, invalid_items INTEGER NOT NULL DEFAULT 0,
    error_category TEXT, source TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS requests_time ON requests(occurred_at,model_version);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
    occurred_at TEXT NOT NULL, model_version TEXT NOT NULL REFERENCES models(version),
    text TEXT NOT NULL, language TEXT NOT NULL, channel TEXT NOT NULL,
    text_length INTEGER NOT NULL, oov_ratio REAL NOT NULL,
    prediction TEXT NOT NULL CHECK(prediction IN ('account','billing','technical')),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    probabilities_json TEXT NOT NULL, anomaly INTEGER NOT NULL,
    inference_ms REAL NOT NULL, source TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS events_time ON events(occurred_at,model_version);
CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
    label TEXT NOT NULL CHECK(label IN ('account','billing','technical')),
    received_at TEXT NOT NULL, source TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS labels_event ON labels(event_id,received_at,id);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
    corrected_label TEXT CHECK(corrected_label IN ('account','billing','technical')),
    comment TEXT NOT NULL, received_at TEXT NOT NULL, external_id TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS feedback_event ON feedback(event_id);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    window_start TEXT NOT NULL, window_end TEXT NOT NULL, model_version TEXT NOT NULL,
    computed_at TEXT NOT NULL, config_sha256 TEXT NOT NULL, metrics_json TEXT NOT NULL,
    PRIMARY KEY(window_start,model_version)
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, severity TEXT NOT NULL,
    window_start TEXT NOT NULL, window_end TEXT NOT NULL, model_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open','resolved')),
    evidence_json TEXT NOT NULL, likely_cause TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_time ON incidents(window_start,model_version);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL, status TEXT NOT NULL, rows_processed INTEGER NOT NULL,
    error TEXT
);
CREATE VIEW IF NOT EXISTS labeled_events AS
SELECT e.*, l.label AS expected_label, l.received_at AS label_received_at
FROM events e LEFT JOIN labels l ON l.id = (
    SELECT id FROM labels WHERE event_id=e.id ORDER BY received_at DESC,id DESC LIMIT 1
);
