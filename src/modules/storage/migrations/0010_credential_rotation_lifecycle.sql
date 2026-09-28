ALTER TABLE credential_rotation_requests ADD COLUMN staged_credential_id TEXT;
ALTER TABLE credential_rotation_requests ADD COLUMN completed_at TEXT;

CREATE INDEX idx_credential_rotation_requests_maintenance
    ON credential_rotation_requests(logical_target, maintenance_operation_id, status);
