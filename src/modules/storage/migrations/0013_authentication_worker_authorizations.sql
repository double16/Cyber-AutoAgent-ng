CREATE TABLE credential_task_authorizations_expanded (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    task_uid TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    authorization_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (logical_target, operation_id, task_uid, credential_id),
    FOREIGN KEY (credential_id) REFERENCES credential_records(credential_id),
    CHECK (authorization_kind IN ('authentication_worker', 'reused_authenticated_context'))
);

INSERT INTO credential_task_authorizations_expanded
    SELECT logical_target, operation_id, task_uid, credential_id, authorization_kind, created_at
    FROM credential_task_authorizations;

DROP TABLE credential_task_authorizations;
ALTER TABLE credential_task_authorizations_expanded RENAME TO credential_task_authorizations;

CREATE INDEX idx_credential_task_authorizations_lookup
    ON credential_task_authorizations(logical_target, operation_id, task_uid, credential_id);
