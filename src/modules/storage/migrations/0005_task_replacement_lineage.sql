ALTER TABLE tasks ADD COLUMN replacement_of TEXT;
ALTER TABLE tasks ADD COLUMN supersedes_criteria TEXT DEFAULT '[]';
