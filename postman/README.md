# Postman collection — Telekom DPS APIs

Postman v2.1 collection covering the 3 DPS HAL APIs called by
`svc/mcp_telekom_identity/dps_get_client.py`:

| API | Path | Skill |
| --- | --- | --- |
| Party Management 3.54.0 | `/party-management/3.54.0/v2/parties` | `dps-party-mgmt-api` |
| Customer Management 4.67.0 | `/customer-management/4.67.0/...` | `dps-customer-mgmt-api` |
| Product Inventory 4.64 | `/product-inventory/4.64/products` | `dps-product-inventory-api` |

## Files

- `telekom-dps.postman_collection.json` — the collection (requests + folders + flows)
- `telekom-dps-test.postman_environment.json` — environment with real test values from
  the `mcp_telekom_identity` README live scenarios

## Import

1. Postman → **Import** → drop both JSON files in.
2. Top-right environment dropdown → select **Telekom DPS — test environment (teai.st.sk)**.
3. Edit the environment → set `bearer_token` (the `APP_DPS_BEARER_TOKEN` value).
4. **VPN required** for `teai.st.sk:8243`.

## What's inside

- **Party Management** — search by RČ / OP / pas / IČO, plus `GET /parties/{id}`
  with optional `hasMatchingSources=true` for dedup investigation.
- **Customer Management** — `GET /customers/{id}`, search by `engagedParty.id`,
  billing account get + sub-resources (`/transactions`, `/accountBalances`,
  `/instalmentPlans`), customer history.
- **Product Inventory** — MSISDN lookup, customer-scoped search, active-tariff
  filter, single product, lightweight `/product-counts`.
- **End-to-end flows** — `RČ → Party → Customer` and `MSISDN → Product → Customer`
  with test scripts that auto-capture IDs into the environment for chaining.

## Auto-headers

The collection has a **pre-request script** that injects the three required
DPS tracing headers on every request:

```
X-Request-Id            ← fresh UUID
X-Request-Tracking-Id   ← fresh UUID
X-Request-Session-Id    ← fresh UUID
```

Override on a per-request basis if you need to trace something specific in
upstream logs.

## Auth

Bearer token, taken from `{{bearer_token}}` environment variable.

## Test scenarios pre-loaded

Values pulled from `svc/mcp_telekom_identity/README.md`:

| Variable | Value | Maps to |
| --- | --- | --- |
| `rodne_cislo` | `7304292105` | Stano Muziková (validated party) |
| `op` | `RC932733` | Stano Muziková |
| `pas` | `BR154151` | Imre Mlynarcik |
| `ico` | `86316923` | Rmc S.R.O. |
| `msisdn` | `421902804660` | (per `OPEN_QUESTIONS.md` MSISDN lookup) |
| `party_id` | `PARTY_1002203200` | Stano Muziková |
| `customer_id` | `1002203200` | Stano Muziková |
| `billing_account_id` | `1002203204` | Stano Muziková's BA |
