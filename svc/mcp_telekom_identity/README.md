# mcp_telekom_identity

MCP server for identifying (and later authenticating) Slovak Telekom customers
against the DPS API (party-management + customer-management).

## Tools

All identification tools share the same response shape:

- **Single match** (1 unique party_id found): `{"found": true, "name": "..."}`
- **Multi match** (more than 1 party): `{"found": true, "multiple_matches": true, "names": [...], "message": "..."}`
- **Not found**: `{"found": false, "error": "not_found", "message": "..."}`
- **Invalid input** (bad format): `{"found": false, "error": "invalid_input", "message": "..."}`
- **System error** (auth/timeout/network/upstream): `{"found": false, "error": "<code>", "message": "Vyskytol sa technický problém. Prepojím vás na operátora."}`

After any successful identification the full candidate set (with `party_id`, `customer_id`,
`contacts`, `identifications`, account info, etc.) is cached in a 30-minute TTL store keyed
by the MCP `X-Conversation-Id` header. Subsequent tools (e.g. account lookup) read this
cache instead of re-querying DPS.

### `identifikacia_rodne_cislo(rodne_cislo)` — Rodné číslo

| Parameter | Format |
|---|---|
| `rodne_cislo` | 9 or 10 digits, no slash |

### `identifikacia_op(cislo_op)` — Občiansky preukaz

| Parameter  | Format                                           |
| ---------- | ------------------------------------------------ |
| `cislo_op` | 2 uppercase letters + 6 digits (e.g. `AB123456`) |

Lowercase letters, spaces, and hyphens in the input are normalized away
(e.g. `ea-123456` → `EA123456`) before validation.

### `identifikacia_pas(cislo_pasu)` — Cestovný pas

| Parameter | Format |
|---|---|
| `cislo_pasu` | 1–2 uppercase letters + 6–8 digits (e.g. `BR154151`) |

### `identifikacia_ico(ico)` — IČO firmy

| Parameter | Format |
|---|---|
| `ico` | Exactly 8 digits |

DPS stores IČO under `identificationType=subjectRegistrationId`.

### `identifikacia_kod_zakaznika(kod_zakaznika)` — Kód zákazníka / fakturačného účtu

| Parameter         | Format                                  |
| ----------------- | --------------------------------------- |
| `kod_zakaznika`   | 8–12 digits (e.g. `4482259100`)         |

The tool dispatches based on the trailing digit:

- Last digit is **`0`** → treated as **Customer ID**, fetched via `GET /customers/{id}`.
- Last digit is **`1–9`** → treated as **Billing Account ID**, fetched via `GET /billingAccounts/{id}` → the linked Customer is then fetched via `GET /customers/{id}`.

For B2C customers DPS stores the name as `"Surname,FirstName"` (no space after the comma).
The tool detects this pattern and presents it as `"FirstName Surname"`. B2B names
(which may legitimately contain a comma followed by a space, e.g. `"Creditinfo Slovakia, S.R.O."`)
are returned verbatim.

## Environment variables

| Var | Default | Notes |
| --- | --- | --- |
| `APP_DPS_BASE_URL` | `https://teai.st.sk:8243/omni/test1` | DPS root URL |
| `APP_DPS_BEARER_TOKEN` | _(empty)_ | Required. Static bearer token. |
| `APP_DPS_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `APP_DPS_VERIFY_TLS` | `false` | Set `true` once a proper CA chain is wired |
| `APP_DPS_MAX_CANDIDATES` | `10` | Cap on Party records before customer fanout |

## Run locally

```bash
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \
APP_MCP_PORT=8765 APP_HEALTHZ_PORT=8766 APP_COLLECT_METRICS=false \
APP_DPS_BEARER_TOKEN="$DPS_TOKEN" APP_DPS_VERIFY_TLS=false \
  python -m svc.mcp_telekom_identity
```

The server then exposes:

| Endpoint | URL | Notes |
| --- | --- | --- |
| MCP (Streamable HTTP) | `http://localhost:8765/mcp` | Paste this URL into mcp-tester or any MCP client |
| Healthz | `http://localhost:8766/healthz` | Liveness/readiness probe |

## Testing via mcp-tester GUI

[`mcp-tester`](https://github.com/Born-Digital-AI/mcp-tester) is a small browser app for
calling MCP tools manually. Start both processes locally:

| Process | URL | Credentials |
| --- | --- | --- |
| `mcp-tester` GUI | `http://localhost:8080` | Basic Auth: `admin` / `admin` |
| `mcp_telekom_identity` (this server) | `http://localhost:8765/mcp` | (no auth — `APP_MCP_AUTH_ENABLED=false`) |

Start `mcp-tester` from `/Users/michaljurco/Documents/GitHub/mcp-tester#` (note the `#` in the path — quote it in shell):

```bash
cd '/Users/michaljurco/Documents/GitHub/mcp-tester#'
APP_BASIC_AUTH_USER=admin APP_BASIC_AUTH_PASSWORD=admin \
APP_SHARE_SECRET=local-dev-only-not-for-prod-aaaaaaaaaaaaaa APP_PORT=8080 \
  python3 app.py
```

Then in the browser:

1. Open `http://localhost:8080` and log in with `admin` / `admin`
2. Paste `http://localhost:8765/mcp` into the MCP URL field
3. Click _List tools_ → vyber konkrétny tool → vyplň parameter → _Call_

End-to-end smoke test from CLI (skips the browser, useful for scripted checks):

```bash
curl -sS -u admin:admin -X POST http://localhost:8080/api/tools/call \
  -H "Content-Type: application/json" \
  -d '{"mcp_url":"http://localhost:8765/mcp","auth":{"type":"none"},
       "tool_name":"identifikacia_rodne_cislo",
       "arguments":{"rodne_cislo":"7304292105"}}'
```

For the reusable workflow (start/stop both processes, common gotchas), see the
[`mcp-local-tester`](file:///Users/michaljurco/.claude/skills/mcp-local-tester/SKILL.md)
skill.

## Live test scenarios (verified against DPS test environment)

These inputs map to known parties in the DPS staging environment. Use them via the
`mcp-tester` GUI above or directly through any MCP client. **VPN required.**

### `identifikacia_rodne_cislo`

| Input | Expected | Backing party |
|---|---|---|
| `7304292105` | `{found, name: "Stano Muziková"}` | PARTY_1002203200 (validated) |
| `8407160630` | `{found, name: "Valent Dorcak"}` | PARTY_4103349400 (validated) |
| `7210055589` | `{found, name: "Imre Mlynarcik"}` | PARTY_1138860700 (initialized) |
| `6862147292` | `{found, name: "Libusa Sotakova"}` | PARTY_1200456600 (initialized) |
| `8753189467` | `{found, multiple_matches: true, names: [...]}` | Test pollution: ~450 records |
| `0000000000` | `{found: false, error: "not_found"}` | — |
| `abc`, `12345` | `{found: false, error: "invalid_input"}` | — |

### `identifikacia_op`

| Input | Expected | Backing party |
|---|---|---|
| `RC932733` | `{found, name: "Stano Muziková"}` | PARTY_1002203200 |
| `HY258342` | `{found, name: "Valent Dorcak"}` | PARTY_4103349400 |
| `MM852148` | `{found, multiple_matches: true, names: [...]}` | Test pollution |
| `XX000000` | `{found: false, error: "not_found"}` | — |
| `123`, `ABCDEF12` | `{found: false, error: "invalid_input"}` | — |

### `identifikacia_pas`

| Input | Expected | Backing party |
|---|---|---|
| `BR154151` | `{found, name: "Imre Mlynarcik"}` | PARTY_1138860700 |
| `XX000000` | `{found: false, error: "not_found"}` | — |
| `123`, `ABCD12345` | `{found: false, error: "invalid_input"}` | — |

### `identifikacia_ico`

| Input | Expected | Backing party |
|---|---|---|
| `86316923` | `{found, name: "Rmc S.R.O."}` | PARTY_2648241400 (organization) |
| `00000000` | `{found: false, error: "not_found"}` | — |
| `1234567`, `abcdefgh` | `{found: false, error: "invalid_input"}` | — |

### `identifikacia_kod_zakaznika`

| Input        | Branch                     | Expected                                            |
| ------------ | -------------------------- | --------------------------------------------------- |
| `4482259100` | Customer ID (B2C)          | `{found, name: "Tester AT NECHYTAT"}`               |
| `1002203200` | Customer ID (B2C)          | `{found, name: "Stano Muziková"}`                   |
| `4103349400` | Customer ID (B2C)          | `{found, name: "Valent Dorcak"}`                    |
| `4059299000` | Customer ID (B2B)          | `{found, name: "A.B.Zrtv"}`                         |
| `2300000400` | Customer ID (B2B)          | `{found, name: "Creditinfo Slovakia, S.R.O."}`      |
| `4108064300` | Customer ID (B2B)          | `{found, name: "J A L & Š, S. R. O."}`              |
| `1002203204` | Billing Account → customer | `{found, name: "Stano Muziková"}`                   |
| `4108064301` | Billing Account → customer | `{found, name: "J A L & Š, S. R. O."}`              |
| `4432948400` | Customer ID (404)          | `{found: false, error: "not_found"}`                |
| `9999999999` | Billing Account (404)      | `{found: false, error: "not_found"}`                |
| `abc`, `12345` | —                        | `{found: false, error: "invalid_input"}`            |
