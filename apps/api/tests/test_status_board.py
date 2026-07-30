"""Unit tests for Status board coercion helpers."""

import pytest

from models import (
    ActionItemUpdate,
    ChunkMetadata,
    IssueUpdate,
    ProjectUpdate,
    TypedEntity,
)
from status_board import _evidence_list, mark_status_item_finished
from storage import (
    _canonical_key,
    _coerce_action_updates,
    _coerce_issue_updates,
    _coerce_project_updates,
)


def _base(**kwargs) -> ChunkMetadata:
    data = dict(
        entities=[],
        knowledge_type="noise",
        ownership=[],
        confidence="low",
        confidence_reason="test",
        summary="summary",
    )
    data.update(kwargs)
    return ChunkMetadata(**data)


def test_canonical_key_normalizes_whitespace() -> None:
    assert _canonical_key("  Email   the vendor ") == "email the vendor"


def test_coerce_action_updates_merges_legacy_strings() -> None:
    meta = _base(
        action_items=["Email the vendor", "email the vendor"],
        action_item_updates=[
            ActionItemUpdate(text="Ship docs", status="open", project="Alpha")
        ],
    )
    updates = _coerce_action_updates(meta)
    texts = sorted(item.text for item in updates)
    assert texts == ["Email the vendor", "Ship docs"]


def test_coerce_issue_updates_synthesizes_from_knowledge_type() -> None:
    meta = _base(
        knowledge_type="problem_report",
        summary="Vendor delay on Alpha Launch",
    )
    updates = _coerce_issue_updates(meta)
    assert len(updates) == 1
    assert updates[0].kind == "problem_report"
    assert updates[0].status == "open"
    assert "Vendor delay" in updates[0].title


def test_coerce_project_updates_from_typed_entities() -> None:
    meta = _base(
        typed_entities=[TypedEntity(name="Alpha Launch", type="project")],
    )
    entity_nodes = [{"name": "Alpha Launch", "type": "project"}]
    updates = _coerce_project_updates(meta, entity_nodes)
    assert updates == [ProjectUpdate(name="Alpha Launch", work_status="open")]


def test_explicit_issue_updates_win() -> None:
    meta = _base(
        knowledge_type="problem_report",
        summary="ignored",
        issue_updates=[
            IssueUpdate(title="Explicit issue", kind="status_update", status="closed")
        ],
    )
    updates = _coerce_issue_updates(meta)
    assert len(updates) == 1
    assert updates[0].title == "Explicit issue"
    assert updates[0].status == "closed"


def test_evidence_list_includes_excerpt_fields() -> None:
    items = _evidence_list(
        [
            {
                "chunk_id": "c1",
                "summary": "Vendor slipped",
                "source": "gmail",
                "source_label": "Thread",
                "knowledge_type": "problem_report",
                "excerpt": "The vendor delayed Alpha.",
            }
        ]
    )
    assert len(items) == 1
    assert items[0].chunk_id == "c1"
    assert items[0].knowledge_type == "problem_report"
    assert items[0].excerpt == "The vendor delayed Alpha."


def test_merge_project_updates_prefers_newer_status() -> None:
    from datetime import datetime, timezone

    from status_board import derive_current_status, merge_project_updates
    from models import StatusEvidence

    older = StatusEvidence(
        chunk_id="c-old",
        summary="Kickoff scheduled",
        knowledge_type="status_update",
        end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = StatusEvidence(
        chunk_id="c-new",
        summary="Vendor delayed Alpha launch by one week",
        knowledge_type="problem_report",
        end_time=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    duplicate = StatusEvidence(
        chunk_id="c-new",
        summary="stale duplicate",
        end_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    merged = merge_project_updates([older, duplicate], [newer])
    assert [item.chunk_id for item in merged] == ["c-new", "c-old"]
    assert derive_current_status(merged) == "Vendor delayed Alpha launch by one week"


def test_derive_current_status_empty() -> None:
    from status_board import derive_current_status

    assert "No recent updates" in derive_current_status([])


@pytest.mark.asyncio
async def test_mark_status_item_finished_requires_id() -> None:
    with pytest.raises(ValueError, match="Item id is required"):
        await mark_status_item_finished("org", "user", "project", "   ")
