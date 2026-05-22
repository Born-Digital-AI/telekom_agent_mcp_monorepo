"""Tests for the DPS HTTP client used by mcp_telekom_identity."""

from __future__ import annotations

import httpx
import pytest
import respx

from lib.boilerplate.logging import current_conversation_id, current_interaction_id
from svc.mcp_telekom_identity.dps_get_client import (
    DPSAuthError,
    DPSError,
    DPSGetClient,
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


def _make_client() -> DPSGetClient:
    return DPSGetClient(
        base_url="https://dps.test/omni/test1",
        bearer_token="TOKEN",  # noqa: S106
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
    assert len(request.headers["x-request-id"]) == 32  # uuid4 hex


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
    assert len(request.headers["x-request-session-id"]) == 32
    assert len(request.headers["x-request-tracking-id"]) == 32


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
async def test_get_raises_dps_auth_error_on_403() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get("/omni/test1/foo").mock(
                return_value=httpx.Response(403, json={"err": "forbidden"}),
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


@pytest.mark.unit
async def test_get_parties_by_identification_calls_correct_path_and_params() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            route = router.get(
                "/omni/test1/party-management/3.54.0/v2/parties",
            ).mock(return_value=httpx.Response(200, json=[{"id": "PARTY_1"}]))
            result = await client.get_parties_by_identification(
                "8753189467",
                "socialSecurityNumber",
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
            result = await client.get_parties_by_identification(
                "0000000000", "socialSecurityNumber"
            )
    assert result == []


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


@pytest.mark.unit
async def test_get_customer_by_id_returns_single_dict_on_200() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            route = router.get(
                "/omni/test1/customer-management/4.67.0/customers/4482259100",
            ).mock(return_value=httpx.Response(200, json={"id": "4482259100", "name": "Tester"}))
            result = await client.get_customer_by_id("4482259100")
    assert result == {"id": "4482259100", "name": "Tester"}
    assert route.calls.last.request.url.params["fields"] == "*"


@pytest.mark.unit
async def test_get_customer_by_id_returns_none_on_404() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get(
                "/omni/test1/customer-management/4.67.0/customers/0000000000",
            ).mock(return_value=httpx.Response(404, json={"error": "not found"}))
            result = await client.get_customer_by_id("0000000000")
    assert result is None


@pytest.mark.unit
async def test_get_customer_by_id_raises_on_other_errors() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get(
                "/omni/test1/customer-management/4.67.0/customers/1234567890",
            ).mock(return_value=httpx.Response(500, json={"error": "boom"}))
            with pytest.raises(DPSUpstreamError) as excinfo:
                await client.get_customer_by_id("1234567890")
    assert excinfo.value.status_code == 500


@pytest.mark.unit
async def test_get_billing_account_by_id_returns_single_dict_on_200() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get(
                "/omni/test1/customer-management/4.67.0/billingAccounts/1002203204",
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"id": "1002203204", "customer": {"id": "1002203200"}},
                )
            )
            result = await client.get_billing_account_by_id("1002203204")
    assert result == {"id": "1002203204", "customer": {"id": "1002203200"}}


@pytest.mark.unit
async def test_get_billing_account_by_id_returns_none_on_404() -> None:
    client = _make_client()
    async with client:
        with respx.mock(base_url="https://dps.test") as router:
            router.get(
                "/omni/test1/customer-management/4.67.0/billingAccounts/9999999999",
            ).mock(return_value=httpx.Response(404, json={"error": "not found"}))
            result = await client.get_billing_account_by_id("9999999999")
    assert result is None
