-- MindLoom operating web pages in the person's Chrome for an automation run
-- (shuttle/browser_tasks.py). The live DDL is in core/schema.py (_SKILL_AUTOMATION_SQL);
-- this file documents it.
ALTER TABLE skill_automations ADD COLUMN IF NOT EXISTS browser_sites TEXT NOT NULL DEFAULT '[]';
ALTER TABLE device_tasks ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT 'mac';
