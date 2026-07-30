"""Unit tests for entity-aware retrieval helpers."""

from retrieval import (
    _heuristic_entities,
    _merge_entity_names,
    normalize_entity_key,
)


def test_normalize_entity_key_collapses_punctuation() -> None:
    assert normalize_entity_key("Mr.Greedy") == "mrgreedy"
    assert normalize_entity_key("Mr. Greedy") == "mrgreedy"
    assert normalize_entity_key("  MR GREEDY ") == "mrgreedy"


def test_heuristic_entities_from_about_phrase() -> None:
    assert _heuristic_entities(
        "What can you tell me about the entity Mr. Greedy"
    ) == ["Mr. Greedy"]
    assert _heuristic_entities("Tell me about Alpha Launch and next steps") == [
        "Alpha Launch"
    ]


def test_heuristic_entities_from_who_is() -> None:
    assert _heuristic_entities("who is Mr. Greedy") == ["Mr. Greedy"]
    assert "Mr.Greedy" in _heuristic_entities("Who is Mr.Greedy?")


def test_merge_entity_names_dedupes_variants() -> None:
    merged = _merge_entity_names(["Mr.Greedy"], ["Mr. Greedy", "Alpha"])
    keys = {normalize_entity_key(name) for name in merged}
    assert keys == {"mrgreedy", "alpha"}
