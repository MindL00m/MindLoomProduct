-- Skill Files that MindLoom performs with tools (shuttle/skill_automation.py).
-- The live DDL is in core/schema.py (_SKILL_AUTOMATION_SQL); this file documents it.
CREATE TABLE IF NOT EXISTS skill_automations (
    automation_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, user_id TEXT NOT NULL,
    skill_id TEXT NOT NULL, plan TEXT NOT NULL DEFAULT '{}',
    auto_approve BOOLEAN NOT NULL DEFAULT FALSE, interval_value INTEGER, interval_unit TEXT,
    next_run_at TIMESTAMPTZ, default_inputs TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, skill_id)
);
CREATE TABLE IF NOT EXISTS skill_automation_runs (
    run_id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, org_id TEXT NOT NULL,
    user_id TEXT NOT NULL, skill_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
    inputs TEXT NOT NULL DEFAULT '{}', messages TEXT NOT NULL DEFAULT '[]',
    log TEXT NOT NULL DEFAULT '[]', pending TEXT, summary TEXT, error TEXT,
    trigger TEXT NOT NULL DEFAULT 'manual', timezone TEXT, rounds INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
