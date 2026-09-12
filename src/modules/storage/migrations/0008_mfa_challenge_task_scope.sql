ALTER TABLE mfa_challenges ADD COLUMN task_uid TEXT;

CREATE INDEX idx_mfa_challenges_task
    ON mfa_challenges(logical_target, operation_id, task_uid, created_at);
