"""Unit tests for shared connector OAuth helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from integrations import _pop_oauth_state, _store_oauth_state, _oauth_states


def test_oauth_state_round_trip():
    _oauth_states.clear()
    state = _store_oauth_state("org-1", "user-1")
    org_id, user_id = _pop_oauth_state(state)
    assert org_id == "org-1"
    assert user_id == "user-1"
    assert state not in _oauth_states


def test_oauth_state_expired():
    _oauth_states.clear()
    state = _store_oauth_state("org-1", "user-1")
    _oauth_states[state] = ("org-1", "user-1", datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(Exception) as exc:
        _pop_oauth_state(state)
    assert "expired" in str(exc.value.detail).lower()
