"""Tests for the DPS HTTP client used by mcp_telekom_identity."""

from __future__ import annotations

import pytest

from svc.mcp_telekom_identity.dps_get_client import (
    DPSAuthError,
    DPSError,
    DPSInvalidResponseError,
    DPSNetworkError,
    DPSTimeoutError,
    DPSUpstreamError,
)


@pytest.mark.unit
def test_error_hierarchy() -> None:
    for cls in (
        DPSAuthError,
        DPSUpstreamError,
        DPSTimeoutError,
        DPSNetworkError,
        DPSInvalidResponseError,
    ):
        assert issubclass(cls, DPSError)


@pytest.mark.unit
def test_upstream_error_carries_status_code() -> None:
    err = DPSUpstreamError(503)
    assert err.status_code == 503
    assert "503" in str(err)
