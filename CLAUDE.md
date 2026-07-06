# CLAUDE.md — telekom_agent_mcp_monorepo

Project memory for Claude Code sessions in this repo. Loaded automatically.

## Project overview

Monorepo of FastMCP services exposing Slovak Telekom backends as MCP tools:

- `svc/mcp_telekom_identity/` — identification & customer lookup (the most active service)
- `svc/mcp_telekom_cc_selfcare/` — customer-care self-care flows
- `svc/mcp_telekom_thd_selfcare/` — T-Home/Telekom self-care flows
- `svc/mcp_template/` — scaffold for new services

The identity service talks to **3 DPS HAL APIs**:

| API | Path | Wired in code? |
| --- | --- | --- |
| Party Management | `/party-management/3.54.0/v2/parties` | yes (`get_parties_by_identification`) |
| Customer Management | `/customer-management/4.67.0/...` | yes (`get_customer_by_id`, `get_billing_account_by_id`, `get_customers_by_engaged_party`) |
| Product Inventory | `/product-inventory/4.64/products` | yes (`get_products_by_public_identifier`) |

Detailed API knowledge lives in user-global skills (auto-triggered):
- `dps-customer-mgmt-api`, `dps-party-mgmt-api`, `dps-product-inventory-api`

## Durable rules

### Keep `postman/` in sync with `dps_get_client.py`

The Postman collection at [`postman/telekom-dps.postman_collection.json`](postman/telekom-dps.postman_collection.json)
must stay aligned with the live set of endpoints called in
[`svc/mcp_telekom_identity/dps_get_client.py`](svc/mcp_telekom_identity/dps_get_client.py).

**When to update the collection**, without being asked:

1. A new method is added to `DPSGetClient` (or any future `DPSPost/PatchClient`) that hits a new endpoint.
2. An existing endpoint's path, query params, or headers change.
3. A new `identificationType` value is wired into `tools.py` (add a "Search by …" request under Party Management).
4. The DPS gateway host changes (update collection `{{base_url}}` variable default and the environment file).
5. New test fixtures land in [`svc/mcp_telekom_identity/README.md`](svc/mcp_telekom_identity/README.md) "Live test scenarios" — refresh the matching env values in [`postman/telekom-dps-test.postman_environment.json`](postman/telekom-dps-test.postman_environment.json) (e.g. `customer_id`, `msisdn`, `party_id`).

**How to update:** edit the JSON files directly. After any change, validate them with:

```bash
python3 -c "import json; json.load(open('postman/telekom-dps.postman_collection.json')); json.load(open('postman/telekom-dps-test.postman_environment.json')); print('OK')"
```

**Conventions to preserve:**

- Collection-level pre-request script auto-injects `X-Request-Id`, `X-Request-Tracking-Id`, `X-Request-Session-Id` as fresh UUIDs (`{{$guid}}`). New requests don't need to re-declare these headers.
- Bearer auth is inherited from `{{bearer_token}}` env var — don't add per-request `Authorization`.
- Group new requests under the matching API folder (`Party Management 3.54.0`, `Customer Management 4.67.0`, `Product Inventory 4.64`). Multi-step flows go under `End-to-end flows`.
- Real test values stay in the environment file, not hardcoded in the collection.

**Skip the update only if:** the code change is purely internal (refactor, logging, retry logic, error mapping) and doesn't change any endpoint surface or test fixture.

**Mention the update in the PR/commit message** that introduced the underlying change, so reviewers know to re-import.

## Other notes

- `docs/OPEN_QUESTIONS.md` tracks unresolved DPS gateway questions (e.g. MSISDN-lookup limitations). Append new findings rather than rewriting.
- Bearer token is provided via `APP_DPS_BEARER_TOKEN` env var, not committed.
- VPN is required to reach `teai.st.sk:8243` from a dev machine.
