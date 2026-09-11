CREATE TABLE finding_evidence_receipts (
    logical_target TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    receipt_uid TEXT NOT NULL,
    source_task_uid TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    marker TEXT NOT NULL,
    artifact_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (logical_target, operation_id, receipt_uid),
    FOREIGN KEY (logical_target, operation_id)
        REFERENCES operations(logical_target, operation_id)
);

CREATE INDEX idx_finding_evidence_receipts_task
    ON finding_evidence_receipts(logical_target, operation_id, source_task_uid, receipt_uid);
