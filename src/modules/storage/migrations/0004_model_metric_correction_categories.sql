ALTER TABLE operation_model_metrics
    ADD COLUMN correction_categories TEXT NOT NULL DEFAULT '{}';
