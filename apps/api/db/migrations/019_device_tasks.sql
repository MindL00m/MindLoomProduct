-- MindLoom operating apps on the person's Mac for an automation run (shuttle/device_tasks.py).
-- The live DDL is in core/schema.py (_SKILL_AUTOMATION_SQL); this file documents it.
ALTER TABLE skill_automations ADD COLUMN IF NOT EXISTS device_apps TEXT NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS device_tasks (
    task_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, org_id TEXT NOT NULL, user_id TEXT NOT NULL,
    skill_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', title TEXT NOT NULL DEFAULT '',
    allowed_apps TEXT NOT NULL DEFAULT '[]', auto_approve BOOLEAN NOT NULL DEFAULT FALSE,
    messages TEXT NOT NULL DEFAULT '[]', log TEXT NOT NULL DEFAULT '[]', pending TEXT,
    summary TEXT, error TEXT, steps INTEGER NOT NULL DEFAULT 0, seq INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
