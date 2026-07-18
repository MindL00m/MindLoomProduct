"""Unit tests for Status board coercion helpers."""

from models import (
    ActionItemUpdate,
    ChunkMetadata,
    IssueUpdate,
    ProjectUpdate,
    TypedEntity,
)
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
