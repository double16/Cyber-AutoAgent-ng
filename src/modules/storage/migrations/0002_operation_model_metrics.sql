CREATE TABLE operation_model_metrics (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    context_window_tokens INTEGER,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    inference_time_ms REAL NOT NULL,
    model_calls INTEGER NOT NULL,
    correction_loops INTEGER NOT NULL,
    efficiency REAL NOT NULL,
    PRIMARY KEY (logical_target, operation_id, captured_at, provider, model),
    FOREIGN KEY (logical_target, operation_id)
        REFERENCES operations(logical_target, operation_id)
);

CREATE INDEX idx_operation_model_metrics_capture
    ON operation_model_metrics(logical_target, operation_id, captured_at, provider, model);
