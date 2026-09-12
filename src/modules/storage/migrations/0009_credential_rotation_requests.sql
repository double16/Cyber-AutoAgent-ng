CREATE TABLE credential_rotation_requests (
    request_id TEXT PRIMARY KEY,
    credential_id TEXT NOT NULL,
    logical_target TEXT NOT NULL,
    request_operation_id TEXT NOT NULL,
    maintenance_operation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    claimed_task_uid TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (credential_id) REFERENCES credential_records(credential_id),
    CHECK (status IN ('queued', 'claimed', 'succeeded', 'failed', 'cancelled'))
);

CREATE INDEX idx_credential_rotation_requests_lookup
    ON credential_rotation_requests(logical_target, status, credential_id, created_at);
