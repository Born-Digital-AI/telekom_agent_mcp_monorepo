"""Tests for the TTL store used by MCP services for transient state."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lib.mcp_service.state import TTLStore


@pytest.mark.unit
def test_set_and_get_returns_value_within_ttl() -> None:
    store: TTLStore[str] = TTLStore(ttl_seconds=10.0)
    store.set("k", "v")
    assert store.get("k") == "v"


@pytest.mark.unit
def test_get_after_ttl_returns_default() -> None:
    store: TTLStore[str] = TTLStore(ttl_seconds=10.0)
    with patch("lib.mcp_service.state.time.monotonic", return_value=1000.0):
        store.set("k", "v")
    with patch("lib.mcp_service.state.time.monotonic", return_value=1015.0):
        assert store.get("k") is None
        assert store.get("k", "fallback") == "fallback"


@pytest.mark.unit
def test_pop_returns_value_then_clears() -> None:
    store: TTLStore[str] = TTLStore(ttl_seconds=10.0)
    store.set("k", "v")
    assert store.pop("k") == "v"
    assert store.get("k") is None


@pytest.mark.unit
def test_contains_respects_ttl() -> None:
    store: TTLStore[int] = TTLStore(ttl_seconds=5.0)
    with patch("lib.mcp_service.state.time.monotonic", return_value=100.0):
        store.set("a", 1)
    with patch("lib.mcp_service.state.time.monotonic", return_value=104.0):
        assert "a" in store
    with patch("lib.mcp_service.state.time.monotonic", return_value=110.0):
        assert "a" not in store


@pytest.mark.unit
def test_sweep_threshold_drops_expired_on_write() -> None:
    """Once the store grows past the threshold, a write triggers a sweep of expired entries."""
    store: TTLStore[int] = TTLStore(ttl_seconds=10.0, sweep_threshold=3)

    with patch("lib.mcp_service.state.time.monotonic", return_value=0.0):
        store.set("a", 1)
        store.set("b", 2)
        store.set("c", 3)

    # 20s later — all of a, b, c are expired.
    with patch("lib.mcp_service.state.time.monotonic", return_value=20.0):
        store.set("d", 4)  # crosses threshold, triggers sweep
        assert len(store) == 1
        assert store.get("d") == 4
        assert store.get("a") is None
