CREATE TABLE credential_records (
    credential_id TEXT PRIMARY KEY,
    logical_target TEXT NOT NULL,
    target TEXT,
    role TEXT,
    operation_id TEXT,
    credential_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    origin TEXT NOT NULL,
    management_policy TEXT NOT NULL,
    status TEXT NOT NULL,
    invalid_at TEXT,
    supersedes_credential_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (credential_type IN ('username_password', 'email_login', 'api_key', 'oauth2_client')),
    CHECK (origin IN ('provided', 'registered', 'found')),
    CHECK (management_policy IN ('user', 'operation')),
    CHECK (status IN ('unknown', 'pending', 'valid', 'invalid', 'expired', 'revoked', 'retired'))
);

CREATE INDEX idx_credential_records_lookup
    ON credential_records(logical_target, target, operation_id, role, status);

CREATE TABLE credential_target_aliases (
    logical_target TEXT NOT NULL,
    canonical_target TEXT NOT NULL,
    alias_target TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (logical_target, canonical_target, alias_target),
    CHECK (created_by = 'user')
);

CREATE TABLE credential_status_events (
    event_id TEXT PRIMARY KEY,
    credential_id TEXT NOT NULL,
    logical_target TEXT NOT NULL,
    operation_id TEXT,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (credential_id) REFERENCES credential_records(credential_id),
    CHECK (status IN ('unknown', 'pending', 'valid', 'invalid', 'expired', 'revoked', 'retired')),
    CHECK (actor IN ('user', 'operation'))
);

CREATE INDEX idx_credential_status_events_credential
    ON credential_status_events(credential_id, created_at);

CREATE TABLE credential_usage_records (
    usage_id TEXT PRIMARY KEY,
    credential_id TEXT NOT NULL,
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    task_uid TEXT,
    authentication_mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (credential_id) REFERENCES credential_records(credential_id),
    CHECK (authentication_mode IN ('authenticated', 'mfa')),
    CHECK (outcome IN ('selected', 'used', 'succeeded', 'failed', 'blocked'))
);

CREATE INDEX idx_credential_usage_operation
    ON credential_usage_records(logical_target, operation_id, credential_id, created_at);

CREATE TABLE mfa_challenges (
    challenge_id TEXT PRIMARY KEY,
    credential_id TEXT NOT NULL,
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (credential_id) REFERENCES credential_records(credential_id),
    CHECK (method IN ('totp', 'email')),
    CHECK (status IN ('pending', 'completed', 'expired', 'blocked'))
);

ALTER TABLE tasks ADD COLUMN auth_context TEXT DEFAULT '{}';
