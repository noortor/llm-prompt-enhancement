CREATE TABLE IF NOT EXISTS bug_reports (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    split TEXT NOT NULL CHECK(split IN ('train', 'eval')),
    gold_severity TEXT NOT NULL,
    gold_component TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY,
    version_number INTEGER NOT NULL,
    label TEXT,
    system_prompt TEXT NOT NULL,
    correction_cutoff TEXT NOT NULL,
    parent_id INTEGER REFERENCES prompt_versions(id),
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS model_outputs (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES bug_reports(id),
    prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
    severity TEXT NOT NULL,
    component TEXT NOT NULL,
    rationale TEXT NOT NULL,
    exemplar_correction_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY,
    output_id INTEGER NOT NULL REFERENCES model_outputs(id),
    report_id INTEGER NOT NULL REFERENCES bug_reports(id),
    original_severity TEXT NOT NULL,
    original_component TEXT NOT NULL,
    original_rationale TEXT NOT NULL,
    corrected_severity TEXT NOT NULL,
    corrected_component TEXT NOT NULL,
    corrected_rationale TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY,
    prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
    severity_accuracy REAL NOT NULL,
    component_accuracy REAL NOT NULL,
    joint_accuracy REAL NOT NULL,
    n_examples INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS eval_results (
    id INTEGER PRIMARY KEY,
    eval_run_id INTEGER NOT NULL REFERENCES eval_runs(id),
    report_id INTEGER NOT NULL REFERENCES bug_reports(id),
    predicted_severity TEXT NOT NULL,
    predicted_component TEXT NOT NULL,
    predicted_rationale TEXT NOT NULL,
    severity_correct INTEGER NOT NULL,
    component_correct INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outputs_report_prompt ON model_outputs(report_id, prompt_version_id);
CREATE INDEX IF NOT EXISTS idx_corrections_created_at ON corrections(created_at);
CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(eval_run_id);
