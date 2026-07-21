"""Export approved Loom workflows into OpenClaw ``SKILL.md`` skill directories.

Loom's ``skill_files.jsonl`` remains the source of truth. The files written here
are *derived artifacts*: one directory per approved extension workflow, laid out
as OpenClaw expects (``<root>/loom-<skill_id>/SKILL.md``). The root is registered
on the OpenClaw host via ``skills.load.extraDirs`` so the browser agent can run
them.

Only ``status == "approved"`` extension skills are exported (same filter as the
Workflows tab); expert-request skills and non-approved drafts are never written.
All operations are best-effort: a failure to write or remove a derived file must
never break approval or the Neo4j ingest that Loom performs.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from config import get_settings
from models import SkillFileDraft

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def is_extension_skill(skill: SkillFileDraft) -> bool:
    """Extension-captured skills drive browser automation; expert answers do not.

    Mirrors ``isExtensionSkill`` in the web client: anything whose session is not
    an ``expert-request:*`` conversation came from the Chrome extension.
    """

    return not skill.session_id.startswith("expert-request:")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug or "workflow"


def _skills_root() -> Path:
    return Path(get_settings().openclaw_skills_dir).expanduser()


def _skill_dir(skill_id: str) -> Path:
    safe = _SLUG_RE.sub("-", skill_id.strip().lower()).strip("-") or "skill"
    return _skills_root() / f"loom-{safe}"


def _section(heading: str, items: list[str]) -> list[str]:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return []
    return ["", f"## {heading}", "", *[f"- {item}" for item in cleaned]]


def render_skill_md(skill: SkillFileDraft) -> str:
    """Render one approved workflow as OpenClaw ``SKILL.md`` text.

    Field mapping (see plan.md Phase 1):
      title  -> frontmatter ``name`` (slugified, ``loom-`` prefixed)
      purpose-> frontmatter ``description`` + body intro
      everything else -> markdown body instructions
    The ``browser.enabled`` config requirement gates the skill on hosts without a
    browser, and a ``loom`` metadata block preserves provenance back to Loom.
    """

    name = f"loom-{_slugify(skill.title)}"
    description = " ".join(skill.purpose.split()) or skill.title
    metadata = {
        "openclaw": {
            "emoji": "🧭",
            "requires": {"config": ["browser.enabled"]},
            "loom": {
                "skill_id": skill.skill_id,
                "application": skill.application,
                "status": skill.status,
                "source": "loom-workflows",
                "session_id": skill.session_id,
            },
        }
    }

    lines: list[str] = [
        "---",
        f"name: {name}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        f"metadata: {json.dumps(metadata, ensure_ascii=False)}",
        "---",
        "",
        f"# {skill.title}",
    ]
    if description:
        lines += ["", description]

    provenance = [f"- **Application:** {skill.application or 'Browser'}", f"- **Loom skill id:** `{skill.skill_id}`"]
    lines += ["", *provenance]

    lines += [
        "",
        "This is an approved Loom workflow. Execute it with the OpenClaw `browser` "
        "tool: snapshot the page before each action, act narrowly on one control at "
        "a time, and re-snapshot after every navigation or form submission. Stop and "
        "ask the user whenever a step is ambiguous or a warning below applies.",
    ]

    lines += _section("When to use", skill.context)

    steps = [str(step).strip() for step in skill.steps if str(step).strip()]
    if steps:
        lines += ["", "## Steps", "", *[f"{index}. {step}" for index, step in enumerate(steps, 1)]]

    lines += _section("Important fields", skill.important_fields)
    lines += _section("Warnings", skill.warnings)
    lines += _section("Decision guidance", skill.decision_guidance)

    notes = skill.expert_notes.strip()
    if notes:
        lines += ["", "## Expert notes", "", notes]

    return "\n".join(lines) + "\n"


def export_skill_file(skill: SkillFileDraft) -> Path | None:
    """Write the ``SKILL.md`` for one approved extension workflow.

    Returns the written path, or ``None`` if the skill is not eligible (expert
    skill or not approved). Never raises: filesystem errors are logged instead so
    approval flow is unaffected.
    """

    if not is_extension_skill(skill) or skill.status != "approved":
        return None
    try:
        skill_dir = _skill_dir(skill.skill_id)
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        path.write_text(render_skill_md(skill), encoding="utf-8")
        logger.info("Exported OpenClaw skill: %s -> %s", skill.skill_id, path)
        return path
    except OSError:
        logger.exception("Failed exporting OpenClaw skill: %s", skill.skill_id)
        return None


def remove_exported_skill(skill_id: str) -> None:
    """Delete a previously exported skill directory, if present. Never raises."""

    try:
        skill_dir = _skill_dir(skill_id)
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            logger.info("Removed OpenClaw skill: %s (%s)", skill_id, skill_dir)
    except OSError:
        logger.exception("Failed removing OpenClaw skill: %s", skill_id)


def sync_skill_file(skill: SkillFileDraft) -> None:
    """Reconcile the derived OpenClaw skill with the latest Loom state.

    Approved extension skills are (re)written; everything else (rejected,
    reverted to proposed, or an expert skill) is removed so stale runnable
    workflows never linger on the host.
    """

    if is_extension_skill(skill) and skill.status == "approved":
        export_skill_file(skill)
    else:
        remove_exported_skill(skill.skill_id)
