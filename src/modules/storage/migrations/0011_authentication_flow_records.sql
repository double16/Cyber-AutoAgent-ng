CREATE TABLE authentication_flow_records (
    flow_id TEXT PRIMARY KEY,
    logical_target TEXT NOT NULL,
    target TEXT NOT NULL,
    purpose TEXT NOT NULL,
    flow_kind TEXT NOT NULL,
    descriptor TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    discovered_operation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_validated_at TEXT,
    CHECK (purpose IN ('authentication', 'registration')),
    CHECK (status IN ('discovered', 'validated', 'invalid'))
);

CREATE INDEX idx_authentication_flow_records_lookup
    ON authentication_flow_records(logical_target, target, purpose, status, updated_at);
