# MCP Telekom Identity — Design Spec

**Date:** 2026-05-21
**Status:** Approved (pending user review of this document)
**Scope:** First slice — `identifikacia_rodne_cislo` tool only

## Purpose

A new MCP server that identifies and (later) authenticates Slovak Telekom customers
against the upstream **DPS** REST API. The first slice exposes one tool:

- `identifikacia_rodne_cislo(rodne_cislo)` — find customer(s) by Slovak
  personal identification number ("rodné číslo", RČ).

Future tools will follow the `identifikacia_<typ>` naming pattern
(`identifikacia_telefon`, `identifikacia_kod_adresata`, `identifikacia_ico`, …).
Authentication (4-digit RČ verification, similar to `mcp_telekom_cc_selfcare`
but against real DPS data) is **out of scope** for this slice and will be
added in a later iteration.

## Background — DPS endpoints

DPS exposes two TMF-like APIs that this service consumes:

| Endpoint | Used for |
| --- | --- |
| `GET /party-management/3.54.0/v2/parties?identificationId=<RČ>&identificationType=socialSecurityNumber&fields=*` | Resolve RČ → `PARTY_id` + party details |
| `GET /customer-management/4.67.0/customers?engagedParty.id=<PARTY_id>&fields=*` | Resolve `PARTY_id` → customer record (status, segments, accounts) |

Real test environment data observed for a single RČ:

- `party-management` returns a JSON array of **906** entries (453 `entityType=Party`,
  status=initialized, and 453 paired `entityType=ContactParty`). All 453 Party
  records have **unique** `id` (`PARTY_4482xxxxxx`) but only **17** unique
  (givenName, familyName) tuples — clearly test data pollution. In production
  one RČ should resolve to 1 (or very few) Party records.
- `customer-management` returns 1 Customer per Party in the typical case
  (`id` = numeric without `PARTY_` prefix; `engagedParty.id` links back).

Self-signed cert in TLS chain on `teai.st.sk:8243` — TLS verification must be
defeasible per environment.

## Architecture

### Service layout

```text
svc/mcp_telekom_identity/
├── __init__.py            # MCPTelekomIdentity service class + config
├── __main__.py            # python -m svc.mcp_telekom_identity
├── requirements.in        # + httpx
├── dps_get_client.py      # DPSGetClient — async HTTP client (GET only)
├── tools.py               # MCP tool: identifikacia_rodne_cislo
└── README.md
```

`dps_get_client.py` exists separately from `tools.py` so the same client can
back future identification tools without duplicating auth/headers/logging.

### Component: `DPSGetClient`

Async wrapper around `httpx.AsyncClient`, one instance per service (lifecycle
bound to `MCPService`).

```python
class DPSGetClient:
    def __init__(
        self,
        base_url: str,            # e.g. "https://teai.st.sk:8243/omni/test1"
        bearer_token: str,
        timeout_seconds: float,
        verify_tls: bool,
    ) -> None: ...

    async def get_parties_by_identification(
        self, identification_id: str, identification_type: str
    ) -> list[dict]:
        """GET /party-management/3.54.0/v2/parties?... -> raw response array."""

    async def get_customers_by_engaged_party(
        self, party_id: str
    ) -> list[dict]:
        """GET /customer-management/4.67.0/customers?... -> raw response array."""

    async def _get(self, path: str, params: dict[str, str]) -> list[dict] | dict:
        """Shared helper: build Authorization + X-Request-* headers, call
        httpx.AsyncClient.get, parse JSON, raise typed errors."""

    async def aclose(self) -> None: ...
```

#### Headers per HTTP call

| Header | Source |
| --- | --- |
| `Authorization` | `"Bearer "` + `APP_DPS_BEARER_TOKEN` |
| `accept` | `application/json` |
| `X-Request-Id` | Fresh `uuid4()` for every HTTP hop |
| `X-Request-Session-Id` | `current_conversation_id.get("")` from MCP ContextVar; falls back to `uuid4()` if empty |
| `X-Request-Tracking-Id` | `current_interaction_id.get("")` from MCP ContextVar; falls back to `uuid4()` if empty |

Headers are composed by `_get`. Tools never construct them.

#### Typed errors

`_get` raises one of:

- `DPSAuthError` — HTTP 401/403
- `DPSUpstreamError(status_code)` — any other 4xx (except 401/403) or 5xx
- `DPSTimeoutError` — `httpx.TimeoutException`
- `DPSNetworkError` — any other `httpx.RequestError`
- `DPSInvalidResponseError` — 2xx with non-JSON or non-list-of-objects body

All defined in `dps_get_client.py`. Tools translate them to user-facing error JSON.

### Component: tool `identifikacia_rodne_cislo`

```python
@mcp_tool(name="identifikacia_rodne_cislo",
          description="<see Tool description below>",
          registry=registry)
async def identifikacia_rodne_cislo(
    rodne_cislo: str,
    _meta: dict | None = None,
) -> str: ...
```

The tool uses the legacy compat shim (`lib.mcp_service.legacy_compat.mcp_tool`)
for consistency with the existing telekom services in this monorepo. Returns
a JSON string.

#### Flow

1. **Validate RČ.** Strip whitespace. Must be 9 or 10 digit characters
   (`re.fullmatch(r"\d{9,10}", value)`). Bad input → return `invalid_input`
   error JSON without making any HTTP call.
2. **Step A — party lookup.**
   `parties = await dps.get_parties_by_identification(rc, "socialSecurityNumber")`
3. **Filter & dedup.**
   - Keep entries with `entityType == "Party"` (drop `ContactParty`).
   - Keep entries with `status == "initialized"` (drop terminated / unset).
   - Dedup by `id` (safety — observed data already has unique ids).
4. **Cap.** If filtered count > `dps_max_candidates` (default 10), keep first
   `dps_max_candidates` and remember `total_party_matches` + `truncated=True`.
5. **Step B — customer fanout.**
   For each kept Party, run
   `customers = await dps.get_customers_by_engaged_party(party.id)` concurrently
   (`asyncio.gather`). Each call can return 0..N customers; flatten.
6. **Normalize & merge.** For each (Party, Customer) pair, build a `candidate`
   record from the merged subset (see "Output schema" below). If a Party has no
   matching Customer, still include it with `customer_id: null` (so the agent
   sees the Party but knows there is no Customer record yet). If a Party has
   N > 1 Customers (rare but possible), emit N candidates sharing the same
   `party_id` — the agent disambiguates.
7. **Return JSON.** See "Output schema".

#### Tool description (LLM-facing)

```text
Identifikuj zákazníka v systéme DPS podľa rodného čísla.

Vstup: rodne_cislo — 9 alebo 10 cifier (bez lomky).

Výstup: JSON so zoznamom kandidátov. Každý kandidát obsahuje party_id,
customer_id, meno, status zákazníka, segment, kontaktné údaje. Ak je
kandidátov viac, vráti maximálne 10 (truncated=true).

Tool sám zreťazí volania DPS party-management a customer-management.
```

### Output schema

#### Success — found

```json
{
  "found": true,
  "total_party_matches": 1,
  "returned_count": 1,
  "truncated": false,
  "candidates": [
    {
      "party_id": "PARTY_4482259100",
      "customer_id": "4482259100",
      "name": "Tester AT NECHYTAT",
      "given_name": "Tester",
      "family_name": "AT NECHYTAT",
      "status": "preactive",
      "market_segment": "Basic",
      "customer_segment": "B2C",
      "treatment_package": "Premium Basic",
      "valid_for": {"start": "2026-02-01T00:00:00Z", "end": null},
      "contacts": [
        {"type": "mobile", "value": "0902555002"},
        {"type": "email", "value": "test@telekom.sk"},
        {"type": "address", "value": "Hubeného 9, 83153 Bratislava"}
      ],
      "identifications": [
        {"type": "nationalIdentityCard", "id": "MM852148"}
      ]
    }
  ]
}
```

Field origins:

- `party_id` — Party `id` (e.g. `PARTY_4482259100`)
- `customer_id` — Customer `id` (numeric without prefix), `null` if Party has
  no Customer in DPS
- `name`, `given_name`, `family_name` — from `party.individual.{givenName,familyName}`
- `status` — from `customer.status` (e.g. `preactive`, `active`, `terminated`)
- `market_segment` — `customer.marketSegment`
- `customer_segment` — `customer.customerSegment`
- `treatment_package` — `customer.characteristics[name=treatmentPackage].value`
- `valid_for` — `customer.validFor` (`{startDateTime, endDateTime}` flattened to `{start, end}`)
- `contacts[]` — derived from `party.contacts[]`:
  - `mobile` → `{type: "mobile", value: medium.number}`
  - `email` → `{type: "email", value: medium.emailAddress}`
  - `address` → `{type: "address", value: <formatted "streetName streetNr, postcode locality">}`
- `identifications[]` — `party.individual.individualIdentifications[]`, but
  **`socialSecurityNumber` entries are dropped** (the caller already provided
  the RČ as input; never echo it back).

#### Success — not found

```json
{
  "found": false,
  "error": "not_found",
  "message": "Pre zadané rodné číslo nebol nájdený žiadny zákazník v systéme DPS."
}
```

#### Validation error

```json
{
  "found": false,
  "error": "invalid_input",
  "message": "Rodné číslo musí mať 9 alebo 10 cifier (bez lomky)."
}
```

#### Auth failure (bad token)

```json
{
  "found": false,
  "error": "auth_failed",
  "message": "Autentifikácia voči systému DPS zlyhala. Skontrolujte konfiguráciu tokenu."
}
```

#### Upstream error / timeout / network

```json
{"found": false, "error": "upstream_error",
 "message": "Systém DPS momentálne nie je dostupný. Skúste o chvíľu znova."}

{"found": false, "error": "upstream_timeout",
 "message": "Systém DPS nestihol odpovedať v limite. Skúste znova."}

{"found": false, "error": "upstream_unreachable",
 "message": "Nedá sa pripojiť k systému DPS. Skontrolujte sieťové pripojenie."}
```

## Configuration

```python
class MCPTelekomIdentityConfig(MCPServiceConfig):
    """Configuration for the Telekom Identity MCP service."""

    mcp_name: str = "mcp-telekom-identity"

    # DPS upstream
    dps_base_url: str = "https://teai.st.sk:8243/omni/test1"
    dps_bearer_token: str = pydantic.Field(default="", exclude=True)  # APP_DPS_BEARER_TOKEN
    dps_timeout_seconds: float = 10.0
    dps_verify_tls: bool = False  # Test env uses self-signed cert chain
    dps_max_candidates: int = 10  # Cap after Step A filter
```

Env vars added to `.env.example`:

```bash
# DPS upstream (Slovak Telekom Omni / party + customer management)
APP_DPS_BASE_URL=https://teai.st.sk:8243/omni/test1
APP_DPS_BEARER_TOKEN=
APP_DPS_TIMEOUT_SECONDS=10
APP_DPS_VERIFY_TLS=false
APP_DPS_MAX_CANDIDATES=10
```

Notes:

- `dps_bearer_token` is a secret (`exclude=True`) — masked in `repr(config)`,
  never logged. Per AGENTS.md.
- `dps_verify_tls=False` default reflects the current test environment. Production
  deployment should set `APP_DPS_VERIFY_TLS=true` once a proper CA chain is wired up.
  (Future improvement: support `APP_DPS_CA_BUNDLE` for an explicit trust store.)

## Logging contract

Per AGENTS.md, the service must use `self.logger` (or module loggers picked up
by the root config). All records carry `application=mcp-telekom-identity`,
`conversation_id`, `interaction_id`, `trace_id` automatically.

Each tool invocation logs:

1. **Entry** — `INFO`, fields: `tool=identifikacia_rodne_cislo`,
   `rc_last4=XXXX` (last 4 digits of input, **never the full RČ**).
2. **Step A request** — `INFO`, fields: `dps_call=party-management`,
   `identification_type=socialSecurityNumber`. **No query string.** **No body.**
3. **Step A response** — `INFO`, fields: `status=200`, `party_count=453`,
   `latency_ms=187`.
4. **Step B fanout** — `INFO`, fields: `dps_call=customer-management`,
   `party_count_after_cap=10`.
5. **Step B response per party** — `DEBUG` (one line per fanout), fields:
   `party_id=PARTY_xxx`, `customer_count=N`, `status=200`, `latency_ms=…`.
6. **Result** — `INFO`, fields: `found=true|false`, `returned_count=N`,
   `truncated=true|false`.

PII rules:

- **Never** log full RČ. Only `rc_last4`.
- **Never** log full Authorization header. Mask to `Bearer ****`.
- **Never** log full names / addresses / contacts. Either drop them or mask
  (e.g. `Tester ***`, `***@telekom.sk`, last 4 digits of MSISDN). Re-use the
  pattern from `svc/mcp_telekom_cc_selfcare/tools.py::_mask_email`.

## Testing

Mirror tree under `tests/svc/mcp_telekom_identity/`:

- `test_dps_get_client.py` — mock httpx with `respx` (already pulled in as a
  transitive test dep; if not, add to `requirements-dev.in`).
  Coverage:
  - 200 with valid JSON list → returns parsed list
  - 200 with empty list → returns `[]`
  - 401 → raises `DPSAuthError`
  - 500 → raises `DPSUpstreamError(500)`
  - `httpx.TimeoutException` → raises `DPSTimeoutError`
  - `httpx.ConnectError` → raises `DPSNetworkError`
  - 200 with non-JSON body → raises `DPSInvalidResponseError`
  - Headers injected correctly (Authorization, X-Request-Id is uuid4 hex,
    X-Request-Session-Id mirrors `current_conversation_id` ContextVar,
    X-Request-Tracking-Id mirrors `current_interaction_id` ContextVar)
- `test_tools.py` — mock `DPSGetClient`.
  Coverage:
  - `rodne_cislo=""` / `"abc"` / `"12345678"` / `"123456789012"` → `invalid_input`
  - Happy path: 1 Party, 1 Customer → `found=true`, `total_party_matches=1`,
    output schema valid
  - Multi-party test pollution: 50 Party records (mocked) → `returned_count=10`,
    `truncated=true`, `total_party_matches=50`
  - Party found, no Customer → candidate with `customer_id=null`
  - `DPSAuthError` from client → `auth_failed`
  - `DPSTimeoutError` → `upstream_timeout`
  - `DPSUpstreamError(500)` → `upstream_error`
  - Logging assertions: caplog records contain `rc_last4` but never the full RČ
    or the Bearer token

Mark all of these `@pytest.mark.unit`. No live API tests in CI — those go
through manual smoke runs.

## Verification (manual, post-implementation)

With VPN to test env, with `APP_DPS_BEARER_TOKEN` exported:

```bash
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \
APP_MCP_PORT=8765 APP_HEALTHZ_PORT=8766 APP_COLLECT_METRICS=false \
APP_DPS_BEARER_TOKEN="$DPS_TOKEN" APP_DPS_VERIFY_TLS=false \
  .venv/bin/python bin/run_service.py mcp_telekom_identity &

sleep 3
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

Expected: HTTP 200, a JSON-RPC response with a `result.content[0].text` whose
parsed body matches the "found" output schema (with `truncated=true`,
`total_party_matches >= 1` in test env).

## Out of scope (this slice)

- Other identification methods (`identifikacia_telefon`, `identifikacia_kod_adresata`,
  `identifikacia_ico`). The client is structured so they can be added by
  introducing a new `identificationType` mapping and a thin tool wrapper.
- The actual **authentication** step (verify last 4 digits of RČ against the
  one returned by DPS). Will be a separate tool (`overenie_rodneho_cisla`?)
  consuming `_AUTH_STATE` and `PARTY_id` from a prior `identifikacia_*` call.
- OAuth2 token refresh — the bearer token is treated as static for now.
- Mutating endpoints (POST/PUT/DELETE). Hence the explicit `dps_get_client.py`
  name; a future `dps_post_client.py` (or extension to a single client) can
  follow when needed.

## Open questions for review

- Should `dps_max_candidates` ever be exceeded — i.e. should the tool support
  pagination via a follow-up argument (e.g. `offset`)? Current design: no
  (YAGNI; agent can re-prompt user for more specific input).
- Should `address` formatting always be `"<streetName> <streetNr>, <postcode> <locality>"`,
  or include `city`/`stateOrProvince`/`country`? Current design: short form
  (street + postcode + locality), since the test data has identical city
  and locality. If real prod data diverges, switch to the long form.
- In Step B, when a Customer has multiple `customerAccounts`, do we surface
  account IDs in the output? Current design: no — accounts are not relevant
  for identification. They'll be surfaced by a future `zoznam_uctov` tool.
