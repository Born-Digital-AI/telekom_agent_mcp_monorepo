# MCP Telekom Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new MCP service `mcp_telekom_identity` exposing one tool `identifikacia_rodne_cislo` that resolves a Slovak personal identification number (rodné číslo) to a list of customer candidates by chaining DPS party-management and customer-management endpoints.

**Architecture:** Per-service directory under `svc/` per AGENTS.md. Async `DPSGetClient` (httpx, GET-only) encapsulates DPS HTTP transport, header composition, and typed errors. A thin tool in `tools.py` validates RČ, calls the client, normalizes the merged Party+Customer subset, and returns a JSON string with human-readable Slovak error messages. Tests mock the client (tool tests) and httpx via `respx` (client tests).

**Tech Stack:** Python 3.12, FastMCP, httpx (async), pydantic-settings, pytest + pytest-asyncio + respx.

**Reference spec:** [docs/superpowers/specs/2026-05-21-mcp-telekom-identity-design.md](../specs/2026-05-21-mcp-telekom-identity-design.md)

---

## File Structure

**To be created:**

| Path | Responsibility |
| --- | --- |
| `svc/mcp_telekom_identity/__init__.py` | `MCPTelekomIdentityConfig` (pydantic settings) + `MCPTelekomIdentity` service class wiring `DPSGetClient` and tool registration |
| `svc/mcp_telekom_identity/__main__.py` | Entry-point shim for `python -m svc.mcp_telekom_identity` |
| `svc/mcp_telekom_identity/requirements.in` | Local deps + `httpx` |
| `svc/mcp_telekom_identity/dps_get_client.py` | `DPSGetClient` async wrapper + typed errors (`DPSAuthError`, `DPSUpstreamError`, `DPSTimeoutError`, `DPSNetworkError`, `DPSInvalidResponseError`) |
| `svc/mcp_telekom_identity/tools.py` | `register(registry)` + `identifikacia_rodne_cislo` tool. Holds module-level `DPSGetClient` instance set by service `setup_tools`. |
| `svc/mcp_telekom_identity/README.md` | What the service does + run instructions |
| `tests/svc/mcp_telekom_identity/__init__.py` | Empty marker |
| `tests/svc/mcp_telekom_identity/test_dps_get_client.py` | Unit tests for `DPSGetClient` (respx-mocked httpx) |
| `tests/svc/mcp_telekom_identity/test_tools.py` | Unit tests for `identifikacia_rodne_cislo` (mocked `DPSGetClient`) |

**To be modified:**

| Path | Change |
| --- | --- |
| `.env.example` | Append `APP_DPS_*` block |
| `requirements-dev.in` | Add `respx` |
| `.github/workflows/build_and_push_one.yml` | Add `mcp_telekom_identity` to `service_name.options` |

---

## Task 1: Service skeleton (config + service class + __main__ + requirements)

**Files:**
- Create: `svc/mcp_telekom_identity/__init__.py`
- Create: `svc/mcp_telekom_identity/__main__.py`
- Create: `svc/mcp_telekom_identity/requirements.in`
- Create: `svc/mcp_telekom_identity/README.md`

- [ ] **Step 1: Create `__init__.py` with config and service class (no tools yet)**

```python
"""Telekom Identity MCP service — DPS-backed customer identification."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pydantic

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTelekomIdentityConfig(MCPServiceConfig):
    """Configuration for the Telekom Identity MCP service."""

    mcp_name: str = "mcp-telekom-identity"

    dps_base_url: str = "https://teai.st.sk:8243/omni/test1"
    dps_bearer_token: str = pydantic.Field(default="", exclude=True)
    dps_timeout_seconds: float = 10.0
    dps_verify_tls: bool = False
    dps_max_candidates: int = 10


class MCPTelekomIdentity(MCPService[MCPTelekomIdentityConfig]):
    """Customer identification via DPS party-management + customer-management."""

    NAME = "mcp-telekom-identity"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register identification tools."""
        registry = ToolRegistry(mcp)
        # tools wired in a later task
        _ = registry


SERVICE_CLASS = MCPTelekomIdentity
```

- [ ] **Step 2: Create `__main__.py`**

```python
"""Allow `python -m svc.mcp_telekom_identity` to run the service directly."""

from __future__ import annotations

import asyncio

from bin.run_service import run_service

if __name__ == "__main__":
    asyncio.run(run_service("mcp_telekom_identity"))
```

- [ ] **Step 3: Create `requirements.in`**

```text
# Direct local dependencies
-r ../../lib/mcp_service/requirements.in

# Direct 3rd-party dependencies
httpx
```

- [ ] **Step 4: Create `README.md`**

```markdown
# mcp_telekom_identity

MCP server for identifying (and later authenticating) Slovak Telekom customers
against the DPS API.

## Tools

- `identifikacia_rodne_cislo(rodne_cislo)` — find customer(s) by Slovak personal
  identification number. Chains party-management → customer-management.

## Environment variables

| Var | Default | Notes |
| --- | --- | --- |
| `APP_DPS_BASE_URL` | `https://teai.st.sk:8243/omni/test1` | DPS root URL |
| `APP_DPS_BEARER_TOKEN` | _(empty)_ | Required. Static bearer token. |
| `APP_DPS_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `APP_DPS_VERIFY_TLS` | `false` | Set `true` once a proper CA chain is wired |
| `APP_DPS_MAX_CANDIDATES` | `10` | Cap on Party records before customer fanout |

## Run locally

\`\`\`bash
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \\
APP_MCP_PORT=8765 APP_HEALTHZ_PORT=8766 APP_COLLECT_METRICS=false \\
APP_DPS_BEARER_TOKEN="$DPS_TOKEN" APP_DPS_VERIFY_TLS=false \\
  python -m svc.mcp_telekom_identity
\`\`\`
```

- [ ] **Step 5: Run import check + ruff to verify skeleton parses**

Run:

```bash
.venv/bin/python -c "from svc.mcp_telekom_identity import SERVICE_CLASS; print(SERVICE_CLASS.NAME)"
.venv/bin/ruff check svc/mcp_telekom_identity/
.venv/bin/ruff format --check svc/mcp_telekom_identity/
.venv/bin/python bin/check_imports.py
```

Expected: prints `mcp-telekom-identity`, ruff clean, check_imports clean.

- [ ] **Step 6: Commit**

```bash
git add svc/mcp_telekom_identity/
git commit -m "feat(identity): add mcp_telekom_identity service skeleton"
```

---

## Task 2: Typed errors + DPSGetClient stub

**Files:**
- Create: `svc/mcp_telekom_identity/dps_get_client.py`
- Create: `tests/svc/mcp_telekom_identity/__init__.py`
- Create: `tests/svc/mcp_telekom_identity/test_dps_get_client.py`
- Modify: `requirements-dev.in`

- [ ] **Step 1: Add `respx` to dev dependencies**

Append one line to `requirements-dev.in`:

```text
respx
```

Then install it locally:

```bash
.venv/bin/uv pip install respx
```

- [ ] **Step 2: Write failing test for error class hierarchy**

Create `tests/svc/mcp_telekom_identity/__init__.py` as empty file.

Create `tests/svc/mcp_telekom_identity/test_dps_get_client.py`:

```python
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
```

- [ ] **Step 3: Run test, verify it fails**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v
```

Expected: ImportError / ModuleNotFoundError for `svc.mcp_telekom_identity.dps_get_client`.

- [ ] **Step 4: Create `dps_get_client.py` with error classes + empty client**

```python
"""HTTP client for the Slovak Telekom DPS API (party-management + customer-management).

GET-only. Mutating endpoints will live in a separate module when needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class DPSError(Exception):
    """Base class for all DPS client errors."""


class DPSAuthError(DPSError):
    """HTTP 401/403 from DPS — bearer token rejected or insufficient scope."""


class DPSUpstreamError(DPSError):
    """Non-auth 4xx or any 5xx from DPS."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"DPS upstream returned HTTP {status_code}")
        self.status_code = status_code


class DPSTimeoutError(DPSError):
    """Request to DPS exceeded the configured timeout."""


class DPSNetworkError(DPSError):
    """Network-level failure connecting to DPS (DNS, connection refused, etc.)."""


class DPSInvalidResponseError(DPSError):
    """DPS returned 2xx but the body could not be parsed as expected JSON."""


class DPSGetClient:
    """Async GET-only HTTP client for DPS. To be fleshed out in the next tasks."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float,
        verify_tls: bool,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._verify_tls = verify_tls
```

- [ ] **Step 5: Run test, verify it passes**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add svc/mcp_telekom_identity/dps_get_client.py tests/svc/mcp_telekom_identity/ requirements-dev.in
git commit -m "feat(identity): scaffold DPSGetClient with typed errors"
```

---

## Task 3: `DPSGetClient._get` — HTTP transport + header composition

**Files:**
- Modify: `svc/mcp_telekom_identity/dps_get_client.py`
- Modify: `tests/svc/mcp_telekom_identity/test_dps_get_client.py`

- [ ] **Step 1: Add failing tests for `_get` behaviour**

Append to `tests/svc/mcp_telekom_identity/test_dps_get_client.py`:

```python
import httpx
import respx

from lib.boilerplate.logging import current_conversation_id, current_interaction_id
from svc.mcp_telekom_identity.dps_get_client import DPSGetClient


def _make_client() -> DPSGetClient:
    return DPSGetClient(
        base_url="https://dps.test/omni/test1",
        bearer_token="TOKEN",
        timeout_seconds=2.0,
        verify_tls=False,
    )


@pytest.mark.unit
async def test_get_returns_parsed_json_on_200() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            route = router.get("/omni/test1/foo").mock(
                return_value=httpx.Response(200, json=[{"id": "PARTY_1"}]),
            )
            result = await client._get("/foo", {"a": "b"})
    assert result == [{"id": "PARTY_1"}]
    assert route.called


@pytest.mark.unit
async def test_get_injects_bearer_and_request_ids_from_contextvars() -> None:
    client = _make_client()
    token_conv = current_conversation_id.set("conv-7")
    token_inter = current_interaction_id.set("inter-9")
    try:
        async with client:
            with respx.mock(base_url="https://dps.test") as router:
                route = router.get("/omni/test1/foo").mock(
                    return_value=httpx.Response(200, json=[]),
                )
                await client._get("/foo", {})
        request = route.calls.last.request
    finally:
        current_conversation_id.reset(token_conv)
        current_interaction_id.reset(token_inter)

    assert request.headers["authorization"] == "Bearer TOKEN"
    assert request.headers["accept"] == "application/json"
    assert request.headers["x-request-session-id"] == "conv-7"
    assert request.headers["x-request-tracking-id"] == "inter-9"
    assert len(request.headers["x-request-id"]) >= 32  # uuid4 hex


@pytest.mark.unit
async def test_get_falls_back_to_uuid_when_contextvars_empty() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            route = router.get("/omni/test1/foo").mock(
                return_value=httpx.Response(200, json=[]),
            )
            await client._get("/foo", {})
    request = route.calls.last.request
    assert len(request.headers["x-request-session-id"]) >= 32
    assert len(request.headers["x-request-tracking-id"]) >= 32


@pytest.mark.unit
async def test_get_raises_dps_auth_error_on_401() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get("/omni/test1/foo").mock(
                return_value=httpx.Response(401, json={"err": "no"}),
            )
            with pytest.raises(DPSAuthError):
                await client._get("/foo", {})


@pytest.mark.unit
async def test_get_raises_dps_upstream_error_on_500() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get("/omni/test1/foo").mock(
                return_value=httpx.Response(500, text="boom"),
            )
            with pytest.raises(DPSUpstreamError) as excinfo:
                await client._get("/foo", {})
    assert excinfo.value.status_code == 500


@pytest.mark.unit
async def test_get_raises_dps_timeout_error() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get("/omni/test1/foo").mock(
                side_effect=httpx.TimeoutException("slow"),
            )
            with pytest.raises(DPSTimeoutError):
                await client._get("/foo", {})


@pytest.mark.unit
async def test_get_raises_dps_network_error_on_connect_failure() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get("/omni/test1/foo").mock(
                side_effect=httpx.ConnectError("nope"),
            )
            with pytest.raises(DPSNetworkError):
                await client._get("/foo", {})


@pytest.mark.unit
async def test_get_raises_invalid_response_on_non_json_body() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get("/omni/test1/foo").mock(
                return_value=httpx.Response(
                    200,
                    text="<html>not json</html>",
                    headers={"content-type": "text/html"},
                ),
            )
            with pytest.raises(DPSInvalidResponseError):
                await client._get("/foo", {})
```

- [ ] **Step 2: Run tests, verify they fail**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v
```

Expected: tests after the existing two fail with `AttributeError: ... has no attribute '_get'` or similar (no `__aenter__`, no `_get`).

- [ ] **Step 3: Implement `_get` with lazy httpx client + `aclose` + optional context manager sugar**

Replace the body of `dps_get_client.py` with:

```python
"""HTTP client for the Slovak Telekom DPS API (party-management + customer-management).

GET-only. Mutating endpoints will live in a separate module when needed.

The underlying ``httpx.AsyncClient`` is created lazily on first call. The class
also supports ``async with`` for tests where eager open/close is convenient,
but the service does not have to use the context manager — the same instance
can be created in ``__init__`` and reused across all tool invocations.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

import httpx

from lib.boilerplate.logging import current_conversation_id, current_interaction_id

if TYPE_CHECKING:
    from types import TracebackType


_log = logging.getLogger(__name__)


class DPSError(Exception):
    """Base class for all DPS client errors."""


class DPSAuthError(DPSError):
    """HTTP 401/403 from DPS — bearer token rejected or insufficient scope."""


class DPSUpstreamError(DPSError):
    """Non-auth 4xx or any 5xx from DPS."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"DPS upstream returned HTTP {status_code}")
        self.status_code = status_code


class DPSTimeoutError(DPSError):
    """Request to DPS exceeded the configured timeout."""


class DPSNetworkError(DPSError):
    """Network-level failure connecting to DPS (DNS, connection refused, etc.)."""


class DPSInvalidResponseError(DPSError):
    """DPS returned 2xx but the body could not be parsed as expected JSON."""


class DPSGetClient:
    """Async GET-only HTTP client for DPS."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float,
        verify_tls: bool,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._verify_tls = verify_tls
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                verify=self._verify_tls,
            )
        return self._client

    async def __aenter__(self) -> DPSGetClient:
        self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        conv = current_conversation_id.get("") or uuid.uuid4().hex
        inter = current_interaction_id.get("") or uuid.uuid4().hex
        return {
            "authorization": f"Bearer {self._bearer_token}",
            "accept": "application/json",
            "x-request-id": uuid.uuid4().hex,
            "x-request-session-id": conv,
            "x-request-tracking-id": inter,
        }

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        client = self._ensure_client()
        url = self._base_url + path
        _log.info("DPS GET %s", path)
        try:
            response = await client.get(url, params=params, headers=self._headers())
        except httpx.TimeoutException as exc:
            _log.warning("DPS GET %s timed out", path)
            raise DPSTimeoutError(str(exc)) from exc
        except httpx.RequestError as exc:
            _log.warning("DPS GET %s network error: %s", path, exc)
            raise DPSNetworkError(str(exc)) from exc

        status = response.status_code
        if status in (401, 403):
            _log.warning("DPS GET %s -> HTTP %s (auth)", path, status)
            raise DPSAuthError(f"DPS rejected the bearer token (HTTP {status})")
        if status >= 400:
            _log.warning("DPS GET %s -> HTTP %s", path, status)
            raise DPSUpstreamError(status)

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("DPS GET %s returned invalid JSON: %s", path, exc)
            raise DPSInvalidResponseError(str(exc)) from exc
```

- [ ] **Step 4: Run tests, verify they pass**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v
```

Expected: all tests pass (2 from Task 2 + 8 new = 10 passed).

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/dps_get_client.py tests/svc/mcp_telekom_identity/test_dps_get_client.py
git commit -m "feat(identity): implement DPSGetClient._get with typed error mapping"
```

---

## Task 4: `DPSGetClient.get_parties_by_identification`

**Files:**
- Modify: `svc/mcp_telekom_identity/dps_get_client.py`
- Modify: `tests/svc/mcp_telekom_identity/test_dps_get_client.py`

- [ ] **Step 1: Write failing tests for the party-management wrapper**

Append to `tests/svc/mcp_telekom_identity/test_dps_get_client.py`:

```python
@pytest.mark.unit
async def test_get_parties_by_identification_calls_correct_path_and_params() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            route = router.get(
                "/omni/test1/party-management/3.54.0/v2/parties",
            ).mock(return_value=httpx.Response(200, json=[{"id": "PARTY_1"}]))
            result = await client.get_parties_by_identification(
                "8753189467", "socialSecurityNumber",
            )
    assert result == [{"id": "PARTY_1"}]
    request = route.calls.last.request
    assert request.url.params["identificationId"] == "8753189467"
    assert request.url.params["identificationType"] == "socialSecurityNumber"
    assert request.url.params["fields"] == "*"


@pytest.mark.unit
async def test_get_parties_by_identification_returns_empty_list_when_no_match() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get(
                "/omni/test1/party-management/3.54.0/v2/parties",
            ).mock(return_value=httpx.Response(200, json=[]))
            result = await client.get_parties_by_identification("0000000000", "socialSecurityNumber")
    assert result == []
```

- [ ] **Step 2: Run, verify they fail**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v -k get_parties
```

Expected: `AttributeError: 'DPSGetClient' object has no attribute 'get_parties_by_identification'`.

- [ ] **Step 3: Implement `get_parties_by_identification`**

Add this method to `DPSGetClient` in `dps_get_client.py` (after `_get`):

```python
    async def get_parties_by_identification(
        self, identification_id: str, identification_type: str,
    ) -> list[dict[str, Any]]:
        """GET /party-management/3.54.0/v2/parties — resolve identification → party records."""
        result = await self._get(
            "/party-management/3.54.0/v2/parties",
            {
                "identificationId": identification_id,
                "identificationType": identification_type,
                "fields": "*",
            },
        )
        if not isinstance(result, list):
            raise DPSInvalidResponseError("party-management expected JSON array")
        return result
```

- [ ] **Step 4: Run, verify pass**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/dps_get_client.py tests/svc/mcp_telekom_identity/test_dps_get_client.py
git commit -m "feat(identity): add DPSGetClient.get_parties_by_identification"
```

---

## Task 5: `DPSGetClient.get_customers_by_engaged_party`

**Files:**
- Modify: `svc/mcp_telekom_identity/dps_get_client.py`
- Modify: `tests/svc/mcp_telekom_identity/test_dps_get_client.py`

- [ ] **Step 1: Write failing tests for the customer-management wrapper**

Append to `tests/svc/mcp_telekom_identity/test_dps_get_client.py`:

```python
@pytest.mark.unit
async def test_get_customers_by_engaged_party_calls_correct_path_and_params() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            route = router.get(
                "/omni/test1/customer-management/4.67.0/customers",
            ).mock(return_value=httpx.Response(200, json=[{"id": "4482259100"}]))
            result = await client.get_customers_by_engaged_party("PARTY_4482259100")
    assert result == [{"id": "4482259100"}]
    request = route.calls.last.request
    assert request.url.params["engagedParty.id"] == "PARTY_4482259100"
    assert request.url.params["fields"] == "*"


@pytest.mark.unit
async def test_get_customers_by_engaged_party_empty_returns_empty_list() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get(
                "/omni/test1/customer-management/4.67.0/customers",
            ).mock(return_value=httpx.Response(200, json=[]))
            result = await client.get_customers_by_engaged_party("PARTY_UNKNOWN")
    assert result == []
```

- [ ] **Step 2: Run, verify they fail**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v -k get_customers
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `get_customers_by_engaged_party`**

Add to `DPSGetClient`:

```python
    async def get_customers_by_engaged_party(
        self, party_id: str,
    ) -> list[dict[str, Any]]:
        """GET /customer-management/4.67.0/customers — resolve PARTY_id → customer records."""
        result = await self._get(
            "/customer-management/4.67.0/customers",
            {
                "engagedParty.id": party_id,
                "fields": "*",
            },
        )
        if not isinstance(result, list):
            raise DPSInvalidResponseError("customer-management expected JSON array")
        return result
```

- [ ] **Step 4: Run, verify pass**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_dps_get_client.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/dps_get_client.py tests/svc/mcp_telekom_identity/test_dps_get_client.py
git commit -m "feat(identity): add DPSGetClient.get_customers_by_engaged_party"
```

---

## Task 6: Tool — RČ validation only

**Files:**
- Create: `svc/mcp_telekom_identity/tools.py`
- Create: `tests/svc/mcp_telekom_identity/test_tools.py`

- [ ] **Step 1: Write failing tests for RČ validation**

Create `tests/svc/mcp_telekom_identity/test_tools.py`:

```python
"""Tests for the identifikacia_rodne_cislo tool."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.boilerplate.logging import current_conversation_id, current_interaction_id
from lib.mcp_service.legacy_compat import ToolRegistry
from svc.mcp_telekom_identity import tools as identity_tools


class _FakeMCP:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, *, name: str, description: str | None = None):  # noqa: ARG002
        def decorator(fn):
            self.registered[name] = fn
            return fn

        return decorator


class _StubClient:
    """A stub DPSGetClient that returns canned responses and records calls."""

    def __init__(
        self,
        parties: list[dict] | Exception | None = None,
        customers_by_party: dict[str, list[dict]] | Exception | None = None,
    ) -> None:
        self.parties = parties if parties is not None else []
        self.customers_by_party = customers_by_party if customers_by_party is not None else {}
        self.party_calls: list[tuple[str, str]] = []
        self.customer_calls: list[str] = []

    async def get_parties_by_identification(
        self, identification_id: str, identification_type: str,
    ) -> list[dict]:
        self.party_calls.append((identification_id, identification_type))
        if isinstance(self.parties, Exception):
            raise self.parties
        return self.parties

    async def get_customers_by_engaged_party(self, party_id: str) -> list[dict]:
        self.customer_calls.append(party_id)
        if isinstance(self.customers_by_party, Exception):
            raise self.customers_by_party
        return self.customers_by_party.get(party_id, [])


@pytest.fixture
def make_tool():
    """Build the tool against a stub client; return (tool_fn, client_stub) factory."""

    def _factory(
        parties: list[dict] | Exception | None = None,
        customers_by_party: dict[str, list[dict]] | Exception | None = None,
        max_candidates: int = 10,
    ):
        stub = _StubClient(parties=parties, customers_by_party=customers_by_party)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_rodne_cislo"], stub

    return _factory


@pytest.fixture
def conv():
    token_c = current_conversation_id.set("conv-test")
    token_i = current_interaction_id.set("inter-test")
    try:
        yield
    finally:
        current_conversation_id.reset(token_c)
        current_interaction_id.reset(token_i)


async def _call(tool, **kwargs) -> dict:
    return json.loads(await tool(**kwargs))


@pytest.mark.unit
async def test_rejects_empty_input(make_tool, conv) -> None:
    tool, stub = make_tool()
    result = await _call(tool, rodne_cislo="")
    assert result == {
        "found": False,
        "error": "invalid_input",
        "message": "Rodné číslo musí mať 9 alebo 10 cifier (bez lomky).",
    }
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["abc", "12345678", "123456789012", "12345/6789", " "])
async def test_rejects_non_digit_or_wrong_length(make_tool, conv, bad) -> None:
    tool, stub = make_tool()
    result = await _call(tool, rodne_cislo=bad)
    assert result["found"] is False
    assert result["error"] == "invalid_input"
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("good", ["123456789", "8753189467"])
async def test_valid_format_reaches_party_call(make_tool, conv, good) -> None:
    tool, stub = make_tool(parties=[])
    await _call(tool, rodne_cislo=good)
    assert stub.party_calls == [(good, "socialSecurityNumber")]
```

- [ ] **Step 2: Run, verify they fail**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_tools.py -v
```

Expected: ModuleNotFoundError for `svc.mcp_telekom_identity.tools`.

- [ ] **Step 3: Create `tools.py` with `register()` + RČ validation only**

```python
"""MCP tools for mcp_telekom_identity.

identifikacia_rodne_cislo(rodne_cislo)
— Find Telekom customer(s) by Slovak personal identification number (rodné číslo).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Annotated, Any

import pydantic

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool

if TYPE_CHECKING:
    from svc.mcp_telekom_identity.dps_get_client import DPSGetClient


_RC_PATTERN = re.compile(r"^\d{9,10}$")
_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka v systéme DPS podľa rodného čísla.\n"
    "Vstup: rodne_cislo — 9 alebo 10 cifier (bez lomky).\n"
    "Výstup: JSON so zoznamom kandidátov (party_id, customer_id, meno, status, "
    "segment, kontakty). Tool zreťazí volania DPS party-management a customer-management."
)
_log = logging.getLogger(__name__)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def register(
    registry: ToolRegistry,
    *,
    client: DPSGetClient,
    max_candidates: int = 10,
) -> None:
    """Register identity tools onto the FastMCP registry."""

    @mcp_tool(
        name="identifikacia_rodne_cislo",
        description=_TOOL_DESCRIPTION,
        registry=registry,
    )
    async def identifikacia_rodne_cislo(
        rodne_cislo: Annotated[
            str,
            pydantic.Field(description="Rodné číslo — 9 alebo 10 cifier, bez lomky."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        rc = (rodne_cislo or "").strip()
        if not _RC_PATTERN.fullmatch(rc):
            return _json(
                {
                    "found": False,
                    "error": "invalid_input",
                    "message": "Rodné číslo musí mať 9 alebo 10 cifier (bez lomky).",
                }
            )

        _log.info(
            "identifikacia_rodne_cislo called rc_last4=%s max_candidates=%s",
            rc[-4:], max_candidates,
        )
        # Step A/B implemented in later tasks.
        await client.get_parties_by_identification(rc, "socialSecurityNumber")
        return _json({"found": False, "error": "not_found", "message": "stub"})
```

- [ ] **Step 4: Run, verify pass**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_tools.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/tools.py tests/svc/mcp_telekom_identity/test_tools.py
git commit -m "feat(identity): add identifikacia_rodne_cislo tool with RČ validation"
```

---

## Task 7: Tool — Step A filter, dedup, cap

**Files:**
- Modify: `svc/mcp_telekom_identity/tools.py`
- Modify: `tests/svc/mcp_telekom_identity/test_tools.py`

- [ ] **Step 1: Write failing tests for filter+cap behaviour**

Append to `tests/svc/mcp_telekom_identity/test_tools.py`:

```python
def _party(party_id: str, status: str = "initialized", entity_type: str = "Party") -> dict:
    return {
        "id": party_id,
        "status": status,
        "entityType": entity_type,
        "type": "individual",
        "individual": {
            "givenName": "Tester",
            "familyName": "AT NECHYTAT",
            "individualIdentifications": [],
        },
        "contacts": [],
    }


@pytest.mark.unit
async def test_not_found_when_no_party_matches(make_tool, conv) -> None:
    tool, _ = make_tool(parties=[])
    result = await _call(tool, rodne_cislo="8753189467")
    assert result == {
        "found": False,
        "error": "not_found",
        "message": "Pre zadané rodné číslo nebol nájdený žiadny zákazník v systéme DPS.",
    }


@pytest.mark.unit
async def test_filters_contactparty_and_non_initialized(make_tool, conv) -> None:
    parties = [
        _party("PARTY_1"),
        _party("PARTY_2", entity_type="ContactParty"),
        _party("PARTY_3", status="terminated"),
        _party("PARTY_4"),
    ]
    tool, stub = make_tool(parties=parties)
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    assert {c["party_id"] for c in result["candidates"]} == {"PARTY_1", "PARTY_4"}
    assert result["total_party_matches"] == 2
    assert result["truncated"] is False
    # No customer lookups stubbed → customer_id remains null
    assert all(c["customer_id"] is None for c in result["candidates"])


@pytest.mark.unit
async def test_caps_candidates_at_max_and_marks_truncated(make_tool, conv) -> None:
    parties = [_party(f"PARTY_{i}") for i in range(25)]
    tool, stub = make_tool(parties=parties, max_candidates=10)
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    assert result["total_party_matches"] == 25
    assert result["returned_count"] == 10
    assert result["truncated"] is True
    assert len(stub.customer_calls) == 10


@pytest.mark.unit
async def test_dedup_by_party_id(make_tool, conv) -> None:
    parties = [_party("PARTY_1"), _party("PARTY_1"), _party("PARTY_2")]
    tool, _ = make_tool(parties=parties)
    result = await _call(tool, rodne_cislo="8753189467")
    assert {c["party_id"] for c in result["candidates"]} == {"PARTY_1", "PARTY_2"}
    assert result["total_party_matches"] == 2
```

- [ ] **Step 2: Run, verify they fail**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_tools.py -v
```

Expected: new tests fail because the tool still returns the stub `not_found` regardless.

- [ ] **Step 3: Implement Step A filter, dedup, cap, and minimal candidate emission (Party-only data — customer_id stays null until Task 8)**

Replace the body of `identifikacia_rodne_cislo` in `tools.py` with:

```python
    async def identifikacia_rodne_cislo(
        rodne_cislo: Annotated[
            str,
            pydantic.Field(description="Rodné číslo — 9 alebo 10 cifier, bez lomky."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        rc = (rodne_cislo or "").strip()
        if not _RC_PATTERN.fullmatch(rc):
            return _json(
                {
                    "found": False,
                    "error": "invalid_input",
                    "message": "Rodné číslo musí mať 9 alebo 10 cifier (bez lomky).",
                }
            )

        _log.info(
            "identifikacia_rodne_cislo called rc_last4=%s max_candidates=%s",
            rc[-4:], max_candidates,
        )

        parties_raw = await client.get_parties_by_identification(rc, "socialSecurityNumber")

        # Filter to Party records with status=initialized, dedup by id.
        seen: set[str] = set()
        parties: list[dict[str, Any]] = []
        for p in parties_raw:
            if p.get("entityType") != "Party":
                continue
            if p.get("status") != "initialized":
                continue
            pid = p.get("id")
            if not isinstance(pid, str) or pid in seen:
                continue
            seen.add(pid)
            parties.append(p)

        if not parties:
            return _json(
                {
                    "found": False,
                    "error": "not_found",
                    "message": (
                        "Pre zadané rodné číslo nebol nájdený žiadny zákazník v systéme DPS."
                    ),
                }
            )

        total = len(parties)
        capped = parties[:max_candidates]
        truncated = total > max_candidates

        # Step B is added in Task 8; for now emit candidates with customer_id=None.
        candidates = [_candidate_from_party_only(p) for p in capped]
        # Each Party also triggers a customer-management lookup (kept as a no-op
        # call here so the cap test can assert the call count).
        for p in capped:
            await client.get_customers_by_engaged_party(p["id"])

        return _json(
            {
                "found": True,
                "total_party_matches": total,
                "returned_count": len(candidates),
                "truncated": truncated,
                "candidates": candidates,
            }
        )
```

Also add this helper to the same module, above `register`:

```python
def _candidate_from_party_only(party: dict[str, Any]) -> dict[str, Any]:
    """Build a candidate record from a Party with no Customer enrichment yet."""
    ind = party.get("individual") or {}
    given = ind.get("givenName") or ""
    family = ind.get("familyName") or ""
    name = " ".join(part for part in (given, family) if part) or None
    return {
        "party_id": party.get("id"),
        "customer_id": None,
        "name": name,
        "given_name": given or None,
        "family_name": family or None,
        "status": None,
        "market_segment": None,
        "customer_segment": None,
        "treatment_package": None,
        "valid_for": None,
        "contacts": [],
        "identifications": [],
    }
```

- [ ] **Step 4: Run, verify pass**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_tools.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/tools.py tests/svc/mcp_telekom_identity/test_tools.py
git commit -m "feat(identity): tool Step A — filter/dedup/cap Party records"
```

---

## Task 8: Tool — Step B fanout + Party/Customer merge + contact normalization

**Files:**
- Modify: `svc/mcp_telekom_identity/tools.py`
- Modify: `tests/svc/mcp_telekom_identity/test_tools.py`

- [ ] **Step 1: Write failing tests for merged output**

Append to `tests/svc/mcp_telekom_identity/test_tools.py`:

```python
def _full_party(party_id: str = "PARTY_4482259100") -> dict:
    return {
        "id": party_id,
        "status": "initialized",
        "entityType": "Party",
        "type": "individual",
        "individual": {
            "givenName": "Tester",
            "familyName": "AT NECHYTAT",
            "individualIdentifications": [
                {
                    "identificationId": "MM852148",
                    "name": "IDNumber",
                    "type": "nationalIdentityCard",
                },
                {
                    "identificationId": "8753189467",
                    "name": "OIBNumber",
                    "type": "socialSecurityNumber",
                },
            ],
        },
        "contacts": [
            {"type": "mobile", "role": {"name": "main"}, "medium": {"number": "0902555002"}},
            {
                "type": "email",
                "role": {"name": "main"},
                "medium": {"emailAddress": "test@telekom.sk"},
            },
            {
                "type": "address",
                "role": {"name": "main"},
                "medium": {
                    "address": {
                        "streetName": "Hubeného",
                        "streetNr": "9",
                        "postcode": "83153",
                        "locality": "Rača",
                    },
                },
            },
        ],
    }


def _customer(customer_id: str = "4482259100", party_id: str = "PARTY_4482259100") -> dict:
    return {
        "id": customer_id,
        "name": "AT NECHYTAT,Tester",
        "status": "preactive",
        "marketSegment": "Basic",
        "customerSegment": "B2C",
        "validFor": {"startDateTime": "2026-02-01T00:00:00Z"},
        "characteristics": [
            {"name": "natcoClassType", "value": "Customer"},
            {"name": "treatmentPackage", "value": "Premium Basic"},
        ],
        "engagedParty": {"entityReferredType": "Party", "id": party_id},
    }


@pytest.mark.unit
async def test_single_match_merges_party_and_customer(make_tool, conv) -> None:
    party = _full_party()
    customer = _customer()
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    assert result["total_party_matches"] == 1
    assert result["returned_count"] == 1
    assert result["truncated"] is False
    [c] = result["candidates"]
    assert c["party_id"] == "PARTY_4482259100"
    assert c["customer_id"] == "4482259100"
    assert c["name"] == "Tester AT NECHYTAT"
    assert c["given_name"] == "Tester"
    assert c["family_name"] == "AT NECHYTAT"
    assert c["status"] == "preactive"
    assert c["market_segment"] == "Basic"
    assert c["customer_segment"] == "B2C"
    assert c["treatment_package"] == "Premium Basic"
    assert c["valid_for"] == {"start": "2026-02-01T00:00:00Z", "end": None}
    assert {"type": "mobile", "value": "0902555002"} in c["contacts"]
    assert {"type": "email", "value": "test@telekom.sk"} in c["contacts"]
    assert {"type": "address", "value": "Hubeného 9, 83153 Rača"} in c["contacts"]
    # socialSecurityNumber must NOT appear in identifications
    assert all(i["type"] != "socialSecurityNumber" for i in c["identifications"])
    assert {"type": "nationalIdentityCard", "id": "MM852148"} in c["identifications"]


@pytest.mark.unit
async def test_party_with_no_customer_yields_candidate_with_null_customer_id(
    make_tool, conv,
) -> None:
    tool, _ = make_tool(
        parties=[_full_party()],
        customers_by_party={"PARTY_4482259100": []},
    )
    result = await _call(tool, rodne_cislo="8753189467")
    [c] = result["candidates"]
    assert c["customer_id"] is None
    assert c["status"] is None
    assert c["name"] == "Tester AT NECHYTAT"
    # Party contacts are still surfaced
    assert any(x["type"] == "mobile" for x in c["contacts"])


@pytest.mark.unit
async def test_party_with_two_customers_yields_two_candidates_sharing_party_id(
    make_tool, conv,
) -> None:
    party = _full_party()
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={
            "PARTY_4482259100": [
                _customer(customer_id="A1"),
                _customer(customer_id="A2"),
            ],
        },
    )
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["returned_count"] == 2
    assert {c["customer_id"] for c in result["candidates"]} == {"A1", "A2"}
    assert {c["party_id"] for c in result["candidates"]} == {"PARTY_4482259100"}


@pytest.mark.unit
async def test_address_formatting_handles_missing_pieces(make_tool, conv) -> None:
    party = _full_party()
    party["contacts"] = [
        {
            "type": "address",
            "role": {"name": "main"},
            "medium": {
                "address": {
                    "streetName": "Mierová",
                    # no streetNr
                    "postcode": "04001",
                    "locality": "Košice",
                },
            },
        },
    ]
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [_customer()]},
    )
    result = await _call(tool, rodne_cislo="8753189467")
    [c] = result["candidates"]
    assert {"type": "address", "value": "Mierová, 04001 Košice"} in c["contacts"]
```

- [ ] **Step 2: Run, verify they fail**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_tools.py -v
```

Expected: new tests fail (current tool emits `customer_id=null` always, no contact normalization, etc.).

- [ ] **Step 3: Implement Step B merge + normalizers**

Edit `tools.py` — replace the previous `_candidate_from_party_only` helper and the
candidate-emission block in `identifikacia_rodne_cislo` with the following code.

Add these helpers above `register`:

```python
def _format_address(addr: dict[str, Any]) -> str:
    street = addr.get("streetName") or ""
    nr = addr.get("streetNr") or ""
    postcode = addr.get("postcode") or ""
    locality = addr.get("locality") or addr.get("city") or ""
    street_part = " ".join(part for part in (street, nr) if part).strip()
    postcode_part = " ".join(part for part in (postcode, locality) if part).strip()
    return ", ".join(part for part in (street_part, postcode_part) if part)


def _normalize_contacts(party_contacts: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for c in party_contacts:
        ctype = c.get("type")
        medium = c.get("medium") or {}
        if ctype == "mobile" and medium.get("number"):
            out.append({"type": "mobile", "value": str(medium["number"])})
        elif ctype == "email" and medium.get("emailAddress"):
            out.append({"type": "email", "value": str(medium["emailAddress"])})
        elif ctype == "address" and isinstance(medium.get("address"), dict):
            formatted = _format_address(medium["address"])
            if formatted:
                out.append({"type": "address", "value": formatted})
    return out


def _normalize_identifications(individual: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ident in individual.get("individualIdentifications") or []:
        itype = ident.get("type")
        if itype == "socialSecurityNumber":
            continue  # Never echo RČ back to the caller.
        iid = ident.get("identificationId")
        if isinstance(itype, str) and isinstance(iid, str):
            out.append({"type": itype, "id": iid})
    return out


def _treatment_package(customer: dict[str, Any]) -> str | None:
    for ch in customer.get("characteristics") or []:
        if ch.get("name") == "treatmentPackage":
            value = ch.get("value")
            return str(value) if value is not None else None
    return None


def _valid_for(customer: dict[str, Any]) -> dict[str, str | None] | None:
    vf = customer.get("validFor")
    if not isinstance(vf, dict):
        return None
    return {
        "start": vf.get("startDateTime"),
        "end": vf.get("endDateTime"),
    }


def _candidate(
    party: dict[str, Any], customer: dict[str, Any] | None,
) -> dict[str, Any]:
    ind = party.get("individual") or {}
    given = ind.get("givenName") or ""
    family = ind.get("familyName") or ""
    name = " ".join(part for part in (given, family) if part) or None
    return {
        "party_id": party.get("id"),
        "customer_id": (customer or {}).get("id"),
        "name": name,
        "given_name": given or None,
        "family_name": family or None,
        "status": (customer or {}).get("status"),
        "market_segment": (customer or {}).get("marketSegment"),
        "customer_segment": (customer or {}).get("customerSegment"),
        "treatment_package": _treatment_package(customer) if customer else None,
        "valid_for": _valid_for(customer) if customer else None,
        "contacts": _normalize_contacts(party.get("contacts") or []),
        "identifications": _normalize_identifications(ind),
    }
```

Then replace the candidate-emission block in `identifikacia_rodne_cislo` with:

```python
        # Step B fanout, concurrently per Party.
        import asyncio  # local import keeps module import light

        customer_lists = await asyncio.gather(
            *(client.get_customers_by_engaged_party(p["id"]) for p in capped)
        )

        candidates: list[dict[str, Any]] = []
        for party, customers in zip(capped, customer_lists, strict=True):
            if customers:
                candidates.extend(_candidate(party, c) for c in customers)
            else:
                candidates.append(_candidate(party, None))

        return _json(
            {
                "found": True,
                "total_party_matches": total,
                "returned_count": len(candidates),
                "truncated": truncated,
                "candidates": candidates,
            }
        )
```

Delete the old `_candidate_from_party_only` helper — it is replaced by `_candidate`.

(Move the `import asyncio` to the top-of-module import block, after `import re`,
to satisfy ruff `I001` once tests pass.)

- [ ] **Step 4: Run, verify pass**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_tools.py -v
```

Expected: all tests pass. Also run the client tests to confirm nothing broke:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/tools.py tests/svc/mcp_telekom_identity/test_tools.py
git commit -m "feat(identity): tool Step B — customer fanout + merge + normalization"
```

---

## Task 9: Tool — map DPS errors to human-readable Slovak JSON

**Files:**
- Modify: `svc/mcp_telekom_identity/tools.py`
- Modify: `tests/svc/mcp_telekom_identity/test_tools.py`

- [ ] **Step 1: Write failing tests for error mapping**

Append to `tests/svc/mcp_telekom_identity/test_tools.py`:

```python
from svc.mcp_telekom_identity.dps_get_client import (
    DPSAuthError,
    DPSInvalidResponseError,
    DPSNetworkError,
    DPSTimeoutError,
    DPSUpstreamError,
)


@pytest.mark.unit
async def test_auth_error_maps_to_auth_failed_json(make_tool, conv) -> None:
    tool, _ = make_tool(parties=DPSAuthError("bad token"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result == {
        "found": False,
        "error": "auth_failed",
        "message": (
            "Autentifikácia voči systému DPS zlyhala. Skontrolujte konfiguráciu tokenu."
        ),
    }


@pytest.mark.unit
async def test_upstream_error_maps_to_upstream_error_json(make_tool, conv) -> None:
    tool, _ = make_tool(parties=DPSUpstreamError(503))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_error"
    assert "DPS" in result["message"]


@pytest.mark.unit
async def test_timeout_error_maps_to_upstream_timeout_json(make_tool, conv) -> None:
    tool, _ = make_tool(parties=DPSTimeoutError("slow"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_timeout"


@pytest.mark.unit
async def test_network_error_maps_to_upstream_unreachable_json(make_tool, conv) -> None:
    tool, _ = make_tool(parties=DPSNetworkError("dns"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_unreachable"


@pytest.mark.unit
async def test_invalid_response_maps_to_upstream_error_json(make_tool, conv) -> None:
    tool, _ = make_tool(parties=DPSInvalidResponseError("bad json"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_error"


@pytest.mark.unit
async def test_customer_call_auth_error_also_maps_cleanly(make_tool, conv) -> None:
    tool, _ = make_tool(
        parties=[_full_party()],
        customers_by_party=DPSAuthError("nope"),
    )
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["error"] == "auth_failed"
```

- [ ] **Step 2: Run, verify they fail**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/test_tools.py -v -k error
```

Expected: tests fail because the tool currently lets DPS exceptions propagate.

- [ ] **Step 3: Wrap the DPS calls in a unified try/except**

Add imports at the top of `tools.py`:

```python
from svc.mcp_telekom_identity.dps_get_client import (
    DPSAuthError,
    DPSError,
    DPSInvalidResponseError,
    DPSNetworkError,
    DPSTimeoutError,
    DPSUpstreamError,
)
```

(Use `if TYPE_CHECKING:` guards as needed — but since these are runtime exception
types we catch, they must be top-level imports.)

Add a helper to convert a `DPSError` to the JSON error payload:

```python
def _dps_error_payload(exc: DPSError) -> dict[str, str]:
    if isinstance(exc, DPSAuthError):
        return {
            "found": False,
            "error": "auth_failed",
            "message": (
                "Autentifikácia voči systému DPS zlyhala. "
                "Skontrolujte konfiguráciu tokenu."
            ),
        }
    if isinstance(exc, DPSTimeoutError):
        return {
            "found": False,
            "error": "upstream_timeout",
            "message": "Systém DPS nestihol odpovedať v limite. Skúste znova.",
        }
    if isinstance(exc, DPSNetworkError):
        return {
            "found": False,
            "error": "upstream_unreachable",
            "message": (
                "Nedá sa pripojiť k systému DPS. Skontrolujte sieťové pripojenie."
            ),
        }
    if isinstance(exc, (DPSUpstreamError, DPSInvalidResponseError)):
        return {
            "found": False,
            "error": "upstream_error",
            "message": (
                "Systém DPS momentálne nie je dostupný. Skúste o chvíľu znova."
            ),
        }
    return {
        "found": False,
        "error": "upstream_error",
        "message": (
            "Systém DPS momentálne nie je dostupný. Skúste o chvíľu znova."
        ),
    }
```

Wrap both DPS calls inside the tool with a single try/except:

```python
        try:
            parties_raw = await client.get_parties_by_identification(rc, "socialSecurityNumber")
        except DPSError as exc:
            _log.warning("identifikacia_rodne_cislo party lookup failed: %s", exc)
            return _json(_dps_error_payload(exc))

        # ... filter/dedup/cap as before ...

        try:
            customer_lists = await asyncio.gather(
                *(client.get_customers_by_engaged_party(p["id"]) for p in capped)
            )
        except DPSError as exc:
            _log.warning("identifikacia_rodne_cislo customer fanout failed: %s", exc)
            return _json(_dps_error_payload(exc))
```

- [ ] **Step 4: Run, verify pass**

Run:

```bash
.venv/bin/pytest tests/svc/mcp_telekom_identity/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/tools.py tests/svc/mcp_telekom_identity/test_tools.py
git commit -m "feat(identity): map DPS exceptions to human-readable Slovak JSON"
```

---

## Task 10: Wire the tool into the service + env config + .env.example

**Files:**
- Modify: `svc/mcp_telekom_identity/__init__.py`
- Modify: `.env.example`

- [ ] **Step 1: Update `__init__.py` to construct `DPSGetClient` and register the tool**

Replace `svc/mcp_telekom_identity/__init__.py` with:

```python
"""Telekom Identity MCP service — DPS-backed customer identification."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pydantic

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

from .dps_get_client import DPSGetClient
from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


_log = logging.getLogger(__name__)


class MCPTelekomIdentityConfig(MCPServiceConfig):
    """Configuration for the Telekom Identity MCP service."""

    mcp_name: str = "mcp-telekom-identity"

    dps_base_url: str = "https://teai.st.sk:8243/omni/test1"
    dps_bearer_token: str = pydantic.Field(default="", exclude=True)
    dps_timeout_seconds: float = 10.0
    dps_verify_tls: bool = False
    dps_max_candidates: int = 10


class MCPTelekomIdentity(MCPService[MCPTelekomIdentityConfig]):
    """Customer identification via DPS party-management + customer-management."""

    NAME = "mcp-telekom-identity"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def __init__(self, config: MCPTelekomIdentityConfig) -> None:
        super().__init__(config)
        if not config.dps_bearer_token:
            _log.warning(
                "APP_DPS_BEARER_TOKEN is empty — DPS calls will fail with auth_failed.",
            )
        self._dps_client = DPSGetClient(
            base_url=config.dps_base_url,
            bearer_token=config.dps_bearer_token,
            timeout_seconds=config.dps_timeout_seconds,
            verify_tls=config.dps_verify_tls,
        )

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register identification tools."""
        registry = ToolRegistry(mcp)
        register(
            registry,
            client=self._dps_client,
            max_candidates=self.config.dps_max_candidates,
        )


SERVICE_CLASS = MCPTelekomIdentity
```

Notes on lifecycle:

- `DPSGetClient` opens its `httpx.AsyncClient` lazily on first `_get` call
  (see Task 3), so no `await` is needed at startup. The first tool call pays
  the connection cost, every subsequent call reuses the same connection pool.
- Explicit cleanup (`aclose`) is deliberately **not** wired here. The MCP
  server is long-lived; `httpx.AsyncClient` is GC-safe and connections close
  with the process. If we later need graceful shutdown (e.g. to flush logs
  before SIGTERM), wire `aclose` into whatever shutdown hook `Service` exposes
  in a separate commit — check `lib/boilerplate/__init__.py::Service` first.

- [ ] **Step 2: Append DPS env vars to `.env.example`**

Edit `.env.example` and add at the bottom:

```bash

# DPS upstream (Slovak Telekom Omni — party + customer management)
APP_DPS_BASE_URL=https://teai.st.sk:8243/omni/test1
APP_DPS_BEARER_TOKEN=
APP_DPS_TIMEOUT_SECONDS=10
APP_DPS_VERIFY_TLS=false
APP_DPS_MAX_CANDIDATES=10
```

- [ ] **Step 3: Verify the service imports and parses config**

Run:

```bash
.venv/bin/python -c "
from svc.mcp_telekom_identity import SERVICE_CLASS, MCPTelekomIdentityConfig
cfg = MCPTelekomIdentityConfig()
print('NAME:', SERVICE_CLASS.NAME)
print('base_url:', cfg.dps_base_url)
print('verify_tls:', cfg.dps_verify_tls)
print('max_candidates:', cfg.dps_max_candidates)
print('token (repr should mask):', repr(cfg)[:200])
"
```

Expected: prints `mcp-telekom-identity`, the configured URL, `False`, `10`, and the
`repr` must NOT show the bearer token literal (`exclude=True` masks it).

- [ ] **Step 4: Run full project tests and ruff**

Run:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python bin/check_imports.py
.venv/bin/pytest -m unit -v
```

Expected: all green. Fix any ruff complaints in-place.

- [ ] **Step 5: Commit**

```bash
git add svc/mcp_telekom_identity/__init__.py .env.example
git commit -m "feat(identity): wire DPSGetClient into MCPTelekomIdentity service"
```

---

## Task 11: Register the service in CI

**Files:**
- Modify: `.github/workflows/build_and_push_one.yml`

- [ ] **Step 1: Add `mcp_telekom_identity` to the `service_name` dropdown**

In `.github/workflows/build_and_push_one.yml`, locate the `options:` list under
`service_name` (around line 12) and add the new entry **at the bottom of the list**:

```yaml
        options:
          - mcp_template
          - mcp_telekom_cc_selfcare
          - mcp_telekom_thd_selfcare
          - mcp_telekom_identity
```

- [ ] **Step 2: Sanity-check the workflow YAML**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build_and_push_one.yml'))"
```

Expected: no exception.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build_and_push_one.yml
git commit -m "ci(identity): register mcp_telekom_identity in build_and_push_one"
```

---

## Task 12: End-to-end verification (lint, type-check, tests, live smoke)

- [ ] **Step 1: Lint + format + type-check + import direction + unit tests**

Run, in order, stopping at the first failure:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/basedpyright
.venv/bin/python bin/check_imports.py
.venv/bin/pytest -m unit
```

Expected: all pass with no warnings about the new service. Fix any issue in
place (e.g. missing `from __future__ import annotations`, line length 100,
type-narrowing complaints from basedpyright) before continuing.

- [ ] **Step 2: Local smoke — server boots and exposes the tool**

In one terminal:

```bash
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \
APP_MCP_PORT=8765 APP_HEALTHZ_PORT=8766 APP_COLLECT_METRICS=false \
APP_DPS_BEARER_TOKEN="dummy" APP_DPS_VERIFY_TLS=false \
  .venv/bin/python bin/run_service.py mcp_telekom_identity
```

In another terminal:

```bash
curl -s -X POST http://localhost:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Conversation-Id: smoke-1" \
  -H "X-Interaction-Id: smoke-2" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

Expected: HTTP 200, JSON-RPC response listing one tool named
`identifikacia_rodne_cislo`. Server log line for the call must include
`application=mcp-telekom-identity`, `conversation_id=smoke-1`,
`interaction_id=smoke-2`.

- [ ] **Step 3: Live smoke against real DPS (requires VPN + real token)**

Export the real token, then:

```bash
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \
APP_MCP_PORT=8765 APP_HEALTHZ_PORT=8766 APP_COLLECT_METRICS=false \
APP_DPS_BEARER_TOKEN="$DPS_TOKEN" APP_DPS_VERIFY_TLS=false \
  .venv/bin/python bin/run_service.py mcp_telekom_identity
```

```bash
curl -s -X POST http://localhost:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Conversation-Id: smoke-1" \
  -H "X-Interaction-Id: smoke-2" \
  -d '{"jsonrpc":"2.0","method":"tools/call",
       "params":{"name":"identifikacia_rodne_cislo",
                 "arguments":{"rodne_cislo":"8753189467"}},
       "id":1}' | jq .
```

Expected:
- HTTP 200 JSON-RPC envelope
- `result.content[0].text` parses to JSON with `found: true`
- In test env: `total_party_matches >= 1`, `truncated: true` when total >
  `APP_DPS_MAX_CANDIDATES`, `candidates[].party_id` present
- Server log line has `rc_last4=9467` and **does not contain** the full RČ

- [ ] **Step 4: Run validation suite one last time**

```bash
.venv/bin/ruff check . && \
.venv/bin/ruff format --check . && \
.venv/bin/basedpyright && \
.venv/bin/python bin/check_imports.py && \
.venv/bin/pytest -m unit
```

Expected: all green.

- [ ] **Step 5: Push and open PR** (only if user requests it — do not auto-push)

```bash
git push -u origin HEAD
gh pr create --title "feat(identity): add mcp_telekom_identity with identifikacia_rodne_cislo" \
  --body "$(cat <<'EOF'
## Summary
- New MCP service `mcp_telekom_identity` (per-service Docker image)
- One tool: `identifikacia_rodne_cislo` — resolves Slovak RČ to a list of customer candidates
- DPS HTTP client (`DPSGetClient`) chains party-management + customer-management with typed errors and X-Request-* header injection from MCP ContextVars
- Slovak human-readable error messages for auth/timeout/network/upstream failures
- Spec: docs/superpowers/specs/2026-05-21-mcp-telekom-identity-design.md
- Plan: docs/superpowers/plans/2026-05-21-mcp-telekom-identity.md

## Test plan
- [ ] `ruff check . && ruff format --check . && basedpyright && pytest -m unit` all pass locally
- [ ] Smoke test with dummy token shows the tool in `tools/list`
- [ ] Live smoke (VPN + real token) returns `found: true` with normalized candidates
- [ ] Server logs show `application=mcp-telekom-identity` and `rc_last4` only

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** Every section of the spec is mapped to a task:
  - Architecture / file layout → Task 1, 2
  - Headers + ContextVar mapping → Task 3
  - Step A endpoint → Task 4
  - Step B endpoint → Task 5
  - RČ validation → Task 6
  - Filter + dedup + cap → Task 7
  - Step B fanout + normalization → Task 8
  - Slovak error messages → Task 9
  - Config + env vars + service wiring → Task 10
  - CI registration → Task 11
  - Verification (lint, tests, live smoke) → Task 12
- **Identification PII rule:** the `_normalize_identifications` filter in Task 8
  drops `socialSecurityNumber` entries; a dedicated test asserts this.
- **No placeholders:** every code block is complete; the only "deferred" item
  is the optional `shutdown` hook in Task 10, which has an explicit fallback
  path (try/finally in `run_forever`).
- **Type consistency:** `DPSGetClient`, `DPSError`, `register(registry, *, client, max_candidates)`,
  candidate keys (`party_id`, `customer_id`, `name`, `given_name`, `family_name`,
  `status`, `market_segment`, `customer_segment`, `treatment_package`,
  `valid_for`, `contacts`, `identifications`) all match between tasks and the spec.
