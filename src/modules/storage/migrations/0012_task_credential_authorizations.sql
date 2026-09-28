CREATE TABLE credential_task_authorizations (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    task_uid TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    authorization_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (logical_target, operation_id, task_uid, credential_id),
    FOREIGN KEY (credential_id) REFERENCES credential_records(credential_id),
    CHECK (authorization_kind IN ('reused_authenticated_context'))
);

CREATE INDEX idx_credential_task_authorizations_lookup
    ON credential_task_authorizations(logical_target, operation_id, task_uid, credential_id);
