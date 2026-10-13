"""LLM JSON often uses entity types outside the graph enum; extraction must still succeed."""

from core.models import ChunkMetadata


def test_date_typed_entities_are_dropped_instead_of_failing_the_chunk():
    metadata = ChunkMetadata.model_validate(
        {
            "entities": ["Project Dessert", "2026-09-01"],
            "knowledge_type": "status_update",
            "ownership": [],
            "confidence": "high",
            "confidence_reason": "Explicit status PDF",
            "summary": "Project Dessert is on track.",
            "typed_entities": [
                {"name": "Project Dessert", "type": "project", "relevance": "primary"},
                {"name": "2026-09-01", "type": "date", "relevance": "secondary"},
                {"name": "Q3 review", "type": "date", "relevance": "secondary"},
                {"name": "Kitchen", "type": "org", "relevance": "secondary"},
            ],
        }
    )
    names = [entity.name for entity in metadata.typed_entities]
    assert names == ["Project Dessert", "Kitchen"]
    assert metadata.typed_entities[0].type == "project"
    assert metadata.typed_entities[1].type == "topic"


def test_empty_valid_until_is_null():
    metadata = ChunkMetadata.model_validate(
        {
            "entities": [],
            "knowledge_type": "status_update",
            "ownership": [],
            "confidence": "low",
            "confidence_reason": "n/a",
            "summary": "A status note.",
            "valid_until": "",
        }
    )
    assert metadata.valid_until is None
