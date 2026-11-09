CREATE TABLE technique_revisions (
    revision_id TEXT PRIMARY KEY,
    lineage TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    origin TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    inactive_reason TEXT NOT NULL DEFAULT '',
    current_revision INTEGER NOT NULL DEFAULT 1,
    document_collection TEXT NOT NULL,
    score_count INTEGER NOT NULL DEFAULT 0,
    score_total INTEGER NOT NULL DEFAULT 0,
    pending_sync INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX idx_technique_current_lineage ON technique_revisions(lineage)
    WHERE current_revision = 1;
CREATE TABLE technique_uses (
    operation_id TEXT NOT NULL,
    task_uid TEXT NOT NULL,
    revision_id TEXT NOT NULL REFERENCES technique_revisions(revision_id),
    score INTEGER CHECK (score IN (-1, 0, 1)),
    reason TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (operation_id, task_uid, revision_id)
);
CREATE TABLE technique_build_leases (
    request_hash TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);
