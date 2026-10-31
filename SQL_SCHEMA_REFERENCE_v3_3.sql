-- Aegis v3.3 reference DDL for simulator-only engineering trials.
-- Operational, timeseries and audit data should be separated by DB boundary in production.
-- This reference executes in one SQLite connection for schema syntax validation only.
-- Not a production migration or a claim of tamper-proof audit.
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  signal_key TEXT NOT NULL,
  value REAL,
  unit TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  received_at_utc TEXT NOT NULL,
  quality TEXT NOT NULL CHECK(quality IN ('GOOD','STALE','BAD','MISSING')),
  device_state_version INTEGER NOT NULL,
  mode_id TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  received_sequence INTEGER,
  CHECK (quality != 'GOOD' OR value IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_obs_dev_signal_time ON observations(device_id,signal_key,observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_obs_dev_mode_time ON observations(device_id,mode_id,observed_at_utc);

-- 1-min summary, one row per device + signal + mode + UTC bucket.
-- Sum/weighted count used to reaggregate; min/max preserved for peaks.
CREATE TABLE IF NOT EXISTS trend_1m (
  device_id TEXT NOT NULL,
  signal_key TEXT NOT NULL,
  mode_id TEXT NOT NULL,
  bucket_start_utc TEXT NOT NULL,
  unit TEXT NOT NULL,
  sample_count INTEGER NOT NULL CHECK(sample_count>=0),
  expected_count INTEGER,
  valid_count INTEGER NOT NULL CHECK(valid_count>=0),
  missing_count INTEGER NOT NULL CHECK(missing_count>=0),
  bad_count INTEGER NOT NULL CHECK(bad_count>=0),
  stale_count INTEGER NOT NULL CHECK(stale_count>=0),
  min_value REAL,
  max_value REAL,
  avg_value REAL,
  first_value REAL,
  last_value REAL,
  sum_value REAL NOT NULL DEFAULT 0,
  sum_squares REAL NOT NULL DEFAULT 0,
  aggregation_version TEXT NOT NULL,
  is_complete INTEGER NOT NULL CHECK(is_complete IN (0,1)),
  PRIMARY KEY (device_id,signal_key,mode_id,bucket_start_utc),
  CHECK(valid_count<=sample_count)
);
CREATE INDEX IF NOT EXISTS idx_trend_1m_time ON trend_1m(device_id,bucket_start_utc);

CREATE TABLE IF NOT EXISTS model_evaluations (
  evaluation_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  mode_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  config_revision TEXT NOT NULL,
  reference_data_version TEXT,
  window_start_utc TEXT NOT NULL,
  window_end_utc TEXT NOT NULL,
  observation_evaluation TEXT NOT NULL CHECK(observation_evaluation IN ('INSUFFICIENT_DATA','NORMAL','ANOMALOUS','UNAVAILABLE')),
  model_lifecycle TEXT NOT NULL CHECK(model_lifecycle IN ('NO_MODEL','SHADOW','ADVISORY','DEGRADED','DISABLED')),
  normal_reference_percentile REAL CHECK(normal_reference_percentile IS NULL OR (normal_reference_percentile>=0 AND normal_reference_percentile<=100)),
  input_quality_ratio REAL CHECK(input_quality_ratio IS NULL OR (input_quality_ratio>=0 AND input_quality_ratio<=1)),
  evidence_keys_json TEXT NOT NULL,
  warning_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_dev_end ON model_evaluations(device_id,window_end_utc);
CREATE INDEX IF NOT EXISTS idx_eval_warning ON model_evaluations(warning_id);

CREATE TABLE IF NOT EXISTS warnings (
  warning_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('OPEN','ACKNOWLEDGED','RESOLVED','MANUAL_REVIEW')),
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  model_version TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_warn_dev_created ON warnings(device_id,created_at_utc);

-- Immutable-at-application-layer append path; DB administrator privileges still matter.
CREATE TABLE IF NOT EXISTS audit_events (
  audit_id TEXT PRIMARY KEY,
  warning_id TEXT,
  occurred_at_utc TEXT NOT NULL,
  actor_pseudonym TEXT NOT NULL,
  action TEXT NOT NULL,
  outcome TEXT NOT NULL,
  request_id TEXT NOT NULL,
  reason_code TEXT,
  FOREIGN KEY (warning_id) REFERENCES warnings(warning_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_once ON audit_events(request_id,action);
CREATE INDEX IF NOT EXISTS idx_audit_warning ON audit_events(warning_id,occurred_at_utc);

CREATE TABLE IF NOT EXISTS service_metric_samples (
  metric_id TEXT NOT NULL,
  source_component TEXT NOT NULL,
  bucket_start_utc TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  labels_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (metric_id,source_component,bucket_start_utc)
);
CREATE INDEX IF NOT EXISTS idx_service_metrics_time ON service_metric_samples(metric_id,bucket_start_utc);
