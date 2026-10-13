"""Unit tests for entity-aware retrieval helpers."""

from brain.retrieval import (
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
    assert _heuristic_entities("who is the CEO of this company") == ["CEO"]


def test_role_search_terms_include_ceo() -> None:
    from brain.retrieval import _role_search_terms

    terms = _role_search_terms("Who is the CEO of this company?", ["CEO"])
    assert "ceo" in terms
    assert "chief executive" in terms


def test_directory_rows_to_chunks_include_title() -> None:
    from brain.retrieval import _directory_rows_to_chunks

    chunks = _directory_rows_to_chunks(
        [{"person_id": "p1", "name": "Ada", "title": "CEO", "email": "ada@x.com", "department": ""}]
    )
    assert chunks[0].chunk_id == "directory:p1"
    assert "CEO" in chunks[0].raw_text
    assert chunks[0].citation is not None
    assert chunks[0].citation.source == "directory"


def test_merge_entity_names_dedupes_variants() -> None:
    merged = _merge_entity_names(["Mr.Greedy"], ["Mr. Greedy", "Alpha"])
    keys = {normalize_entity_key(name) for name in merged}
    assert keys == {"mrgreedy", "alpha"}
