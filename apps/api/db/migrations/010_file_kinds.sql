-- Files of other kinds (TXT, DOCX, XLSX, PDF) and generated versions.
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'csv';
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS mime_type TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS size_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS blob_key TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS previous_blob_key TEXT;
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS based_on_file_id TEXT;
