CREATE TABLE operations (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (logical_target, operation_id)
);

CREATE TABLE plans (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    objective TEXT,
    current_phase INTEGER,
    total_phases INTEGER,
    assessment_complete BOOLEAN,
    plan_data TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (logical_target, operation_id),
    FOREIGN KEY (logical_target, operation_id)
        REFERENCES operations(logical_target, operation_id)
);

CREATE TABLE tasks (
    logical_target TEXT NOT NULL,
    task_uid TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    title TEXT,
    objective TEXT,
    acceptance_contract TEXT NOT NULL,
    phase INTEGER,
    status TEXT,
    status_reason TEXT,
    evidence TEXT,
    created_at TEXT,
    updated_at TEXT,
    kind TEXT DEFAULT 'standard',
    reference_id TEXT,
    target_scope TEXT DEFAULT 'all',
    target_ids TEXT DEFAULT '[]',
    PRIMARY KEY (logical_target, operation_id, task_uid),
    FOREIGN KEY (logical_target, operation_id)
        REFERENCES operations(logical_target, operation_id)
);

CREATE INDEX idx_tasks_operation
    ON tasks(logical_target, operation_id);

CREATE TABLE operation_preflight_results (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target TEXT NOT NULL,
    target_type TEXT NOT NULL,
    status TEXT NOT NULL,
    checks TEXT NOT NULL,
    reason TEXT NOT NULL,
    resolved_addresses TEXT NOT NULL,
    has_global_address BOOLEAN NOT NULL,
    has_private_or_reserved_address BOOLEAN NOT NULL,
    route_reachable BOOLEAN NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY(logical_target, operation_id, target_id),
    FOREIGN KEY (logical_target, operation_id)
        REFERENCES operations(logical_target, operation_id)
);

CREATE TABLE task_acceptance_results (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    task_uid TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    status TEXT NOT NULL,
    disposition TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    coverage TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(logical_target, operation_id, task_uid, criterion_id),
    FOREIGN KEY(logical_target, operation_id, task_uid)
        REFERENCES tasks(logical_target, operation_id, task_uid)
);

CREATE INDEX idx_acceptance_results_operation
    ON task_acceptance_results(logical_target, operation_id);

CREATE TABLE task_acceptance_memory_publications (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    task_uid TEXT NOT NULL,
    publication_key TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(logical_target, operation_id, task_uid),
    FOREIGN KEY(logical_target, operation_id, task_uid)
        REFERENCES tasks(logical_target, operation_id, task_uid)
);

CREATE TABLE finding_records (
    logical_target TEXT NOT NULL,
    finding_uid TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    candidate_data TEXT NOT NULL,
    verification_task_uid TEXT NOT NULL,
    validation_data TEXT,
    resolution TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(logical_target, operation_id, finding_uid),
    UNIQUE(logical_target, operation_id, fingerprint),
    FOREIGN KEY(logical_target, operation_id)
        REFERENCES operations(logical_target, operation_id)
);

CREATE TABLE objective_validation_records (
    logical_target TEXT NOT NULL,
    candidate_uid TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    candidate_data TEXT NOT NULL,
    verification_task_uid TEXT NOT NULL,
    validation_data TEXT,
    resolution TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(logical_target, operation_id, candidate_uid),
    UNIQUE(logical_target, operation_id, fingerprint),
    FOREIGN KEY(logical_target, operation_id)
        REFERENCES operations(logical_target, operation_id)
);
