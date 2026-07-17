"""Tests for knowledge-graph debug export helpers."""

from __future__ import annotations

from storage import _graph_node_id, _serialize_props


def test_graph_node_id_person():
    node_id = _graph_node_id(
        ["Person"],
        {"person_id": "p-1", "canonical_name": "Ada"},
    )
    assert node_id == "p-1"


def test_graph_node_id_chunk():
    node_id = _graph_node_id(["Chunk"], {"chunk_id": "c-99"})
    assert node_id == "c-99"


def test_serialize_props_flattens_nested():
    props = _serialize_props({"name": "Ada", "groups": ["eng", "platform"]})
    assert props["name"] == "Ada"
    assert props["groups"] == ["eng", "platform"]
