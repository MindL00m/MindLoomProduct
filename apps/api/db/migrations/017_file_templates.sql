-- A file marked as a template ({{placeholders}} filled to make new documents).
ALTER TABLE loombot_files ADD COLUMN IF NOT EXISTS is_template BOOLEAN NOT NULL DEFAULT false;
