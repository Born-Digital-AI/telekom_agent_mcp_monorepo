# Identification Flow — Review Document

> **Audience:** DPS API owners (Party Management, Customer Management, Product Inventory)
> and Born-Digital Security. This document is the source of truth for the
> identification flow shipped by `mcp_telekom_identity` and is maintained
> alongside the code. The point is to surface **what we call, with which
> parameters, and how we treat the response** so that owners can confirm we are
> using their APIs as intended and so security can sign off on PII handling
> before the service goes to production.

**Service repository:** `telekom_agent_mcp_monorepo` · branch `feat/mcp-telekom-identity`
**Service directory:** `svc/mcp_telekom_identity/`
**Last verified live (test env):** 2026-05-22

---

## 1. Purpose

`mcp_telekom_identity` is an MCP (Model Context Protocol) server consumed by a
voice AI agent on the Slovak Telekom customer-care line. Its job is **only
identification** — given an identifier the caller provides (rodné číslo,
občiansky preukaz, pas, IČO, customer/billing code, phone number, or device
serial number), look the customer up in DPS and return their name. The full record (`party_id`,
`customer_id`, contacts, accounts) is cached internally for follow-up tools
(account lookup, bill resend, …) which will be added later.

**It does not authenticate or authorise.** Authentication (verifying that the
caller is the customer they claim to be — e.g. comparing the last 4 digits of
RČ against the DPS record) is a separate concern handled by another tool
outside the identification surface.

---

## 2. Tools and the APIs they call

Seven tools, all served by a single MCP server. Each one returns the same
minimal response shape (`{"found": true, "name": "..."}` on success); the
internal flow differs.

| # | Tool                            | Caller input             | API(s) called                                                                 |
| - | ------------------------------- | ------------------------ | ----------------------------------------------------------------------------- |
| 1 | `identifikacia_rodne_cislo`     | rodné číslo (9–10 digits) | Party Management → Customer Management                                        |
| 2 | `identifikacia_op`              | občiansky preukaz (`AB123456`) | Party Management → Customer Management                                  |
| 3 | `identifikacia_pas`             | passport (`BR154151`)    | Party Management → Customer Management                                        |
| 4 | `identifikacia_ico`             | IČO (8 digits)           | Party Management → Customer Management (organization branch)                  |
| 5 | `identifikacia_kod_zakaznika`   | numeric code (8–12)      | Customer Management direct (or Billing Account → Customer)                    |
| 6 | `identifikacia_telefon`         | telefónne číslo (SK or intl) | Product Inventory → Customer Management                                   |
| 7 | `identifikacia_seriove_cislo`   | sériové číslo (`M91450EB0603`) | Product Inventory → Customer Management                               |

---

## 3. Per-tool specification

For every tool: caller input → format normalization → HTTP call(s) → response.
Customer-facing error messages in §6.

### 3.1 `identifikacia_rodne_cislo`

**Caller input:** rodné číslo, 9 or 10 digits, no slash. Validation regex
`^\d{9,10}$`.

**API call A — Party Management:**

```http
GET /party-management/3.54.0/v2/parties
  ?identificationId=<rc>
  &identificationType=socialSecurityNumber
  &fields=*
```

**Post-processing:**

- Filter to `entityType == "Party"` (drops `ContactParty`).
- Drop `status ∈ {deceased, closed}`. Accept `initialized`, `validated`, and
  records with missing/null `status` (observed for some organisations).
- Deduplicate by `Party.id`.
- Cap at `APP_DPS_MAX_CANDIDATES` (default 10).

**API call B — Customer Management (per kept Party, concurrently):**

```http
GET /customer-management/4.67.0/customers?engagedParty.id=<PARTY_id>&fields=*
```

**Result:** if exactly one unique `party_id` → `{found, name}`. If more → multi-match
shape with `names: [...]`.

**Verified live test cases:** `7304292105` → Stano Muziková, `8407160630` →
Valent Dorcak, `7210055589` → Imre Mlynarcik, `6862147292` → Libusa Sotakova,
`8753189467` → multi-match (test env data pollution).

### 3.2 `identifikacia_op`

Same flow as 3.1 but with `identificationType=nationalIdentityCard`. Input is
normalized (strip whitespace and hyphens, uppercase letters) and validated
against `^[A-Z]{2}\d{6}$` — the official Slovak format since 1993. Pre-1993
purely numeric IDs are not in active DPS records and are rejected at validation.

**Verified live test cases:** `RC932733` → Stano Muziková, `HY258342` → Valent
Dorcak, `MM852148` → multi-match (test env data pollution).

### 3.3 `identifikacia_pas`

Same flow as 3.1 but with `identificationType=passport`. Input validated against
`^[A-Z]{1,2}\d{6,8}$` (accepts SK and most international passport formats).

**Verified live test case:** `BR154151` → Imre Mlynarcik.

### 3.4 `identifikacia_ico`

Same flow as 3.1 but with `identificationType=subjectRegistrationId` and
**organisation-branch name extraction**: when `party.type == "organization"`,
the candidate's `name` comes from `party.organization.tradingName` (fallback
`organization.name`, then `party.name`), not from `individual.givenName/familyName`.
Identifications are read from `party.organization.organizationIdentifications`
instead of `individualIdentifications`.

**Verified live test case:** `86316923` → `Rmc S.R.O.` (PARTY_2648241400).

**Note for owners:** the choice of `subjectRegistrationId` for SK IČO was
derived from a live probe (the company `Rmc S.R.O.` has its 8-digit IČO stored
under `Identification.type == "subjectRegistrationId"`, name `registrationNumber`).
The Identification enum in the swagger does not document this Slovak mapping
explicitly — confirmation requested.

### 3.5 `identifikacia_kod_zakaznika`

**Caller input:** numeric code, 8–12 digits. The trailing digit dispatches
between two flows (heuristic verified live across 5 customers):

- **Last digit `0`** → treat as Customer ID:

  ```http
  GET /customer-management/4.67.0/customers/<id>?fields=*
  ```

  Returns a single Customer record (or HTTP 404 → `not_found`). No Party fetch.

- **Last digit `1–9`** → treat as Billing Account ID:

  ```http
  GET /customer-management/4.67.0/billingAccounts/<id>?fields=*
  ```

  Extract `customer.id` from the response, then fetch the Customer as above.

**B2C name reversal:** `Customer.name` for individuals is stored as
`"Surname,FirstName"` (comma, no space). The tool detects the pattern
`(B2C AND single comma AND no whitespace immediately after the comma)` and
reverses it to `"FirstName Surname"`. B2B names (which may have a legitimate
comma followed by a space, e.g. `"Creditinfo Slovakia, S.R.O."`) are not touched.

**Verified live test cases:**

- `4482259100` (B2C) → `Tester AT NECHYTAT` (after reversal)
- `1002203200` (B2C) → `Stano Muziková`
- `4059299000` (B2B) → `A.B.Zrtv`
- `2300000400` (B2B) → `Creditinfo Slovakia, S.R.O.`
- `1002203204` (billing acc) → `Stano Muziková` (cust 1002203200)
- `4108064301` (billing acc) → `J A L & Š, S. R. O.` (cust 4108064300)
- `4432948400` (unknown) → `not_found`

**Note for owners:** the "trailing 0 = customer, 1–9 = billing account"
heuristic was derived from observing 7+ real customers. If this is not a
formal contract guaranteed across all of DPS, please flag — the dispatch
would need adjustment.

### 3.6 `identifikacia_telefon`

**Caller input:** telephone number in SK local form (`0904...`),
international with plus (`+421904...`), international without plus
(`421904...`), or with `00` prefix. Spaces, dashes, parentheses, and dots are
stripped. The cleaned form must match `^421\d{9}$` after normalization
(SK country code + 9 digits).

**API call A — Product Inventory:**

```http
GET /product-inventory/4.64/products
  ?query=publicIdentifier==<intl-msisdn>
  &fields=*
  &size=20
```

The RQL `publicIdentifier==<msisdn>` is the **only working filter** found
during the live probe (see [docs/OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) Q1).
Three alternative DPS APIs (party-management identificationType=msisdn,
party-management `?phoneNumber=`, customer-management RQL on `contacts`) were
tested and **do not work** — they return either empty arrays or time out.

**Post-processing:**

- Collect unique `customer.id` from each returned product.
- For each unique customer, call `GET /customer-management/4.67.0/customers/<id>?fields=*`.
- Apply the same B2C name reversal from 3.5.

**Verified live test cases:**

- `0902804660` → `Stano Muziková` (PARTY_1002203200, intl `421902804660`)
- `+421902804660`, `421902804660`, `0902 804 660`, `0902-804-660` → same
- `0000000000` → `not_found`

**Note for owners:** confirmation requested that `publicIdentifier` semantics
on Product Inventory will remain stable — i.e. mobile tariff products will
continue to use the MSISDN as `publicIdentifier`. If the schema changes (e.g.
move MSISDN into a separate field or normalize stored format), the tool breaks.

### 3.7 `identifikacia_seriove_cislo`

**Caller input:** sériové číslo zariadenia (router, STB, modem). Input is
normalized (strip whitespace, dashes, slashes, dots; uppercase letters) and
validated against `^[A-Z0-9]{8,30}$`.

**API call A — Product Inventory:**

```http
GET /product-inventory/4.64/products
  ?query=productSerialNumber==<sn>
  &fields=*
  &size=20
```

The RQL filter on `productSerialNumber` is case-sensitive in DPS — normalization
is mandatory before the query. Unknown serials return an empty list (no error).

**API call B — Customer Management** (per unique `customer.id`, concurrently):
same as 3.6 (`GET /customers/{id}`). Applies B2C name reversal.

**Verified live test cases:**

- `M91450EB0603` → Stano Muziková (Magio Box s HDD)
- `K5D0M374LXO` → Stano Muziková (Magio Box bez HDD)
- `J252BS000119` → Stano Muziková (HAG)
- `m91450eb0603` (lowercase) / `M9145-0EB-0603` (with hyphens) → normalized then same as first

**Note for owners:** `Product.productSerialNumber` is the canonical field per
the Product Inventory swagger ("A serial number for the product, e.g. for
broadband routers"). Confirmation requested that this field is populated
across all device types (router / STB / SIM / modem) consistently in production.

---

## 4. Shared data flow

```
caller input
    │
    ▼
[Tool-specific validation + normalization]
    │
    ▼
[DPS API call(s)]
    │
    ▼
[Filter / dedup / merge into "candidate" record]
    │
    ├──▶ Response to LLM:  {found: true, name: "..."}            ← minimal
    │
    ├──▶ TTL cache (process-local, 30 min):
    │         key   = X-Conversation-Id
    │         value = {
    │           identification_method:  "rodne_cislo" | "op" | ...
    │           identification_value:   "<full normalized input>"
    │           rc_last4:               "XXXX"
    │           candidates:             [<full record>]
    │         }
    │
    └──▶ Fire-and-forget PUT to NLP engine (`APP_GOODBOT_URL`):
              /conversations/<X-Conversation-Id>/states
              body = {
                named_entities: {
                  identification_method:  "<method>",
                  identification:         <full value | "last4=XXXX">,
                }
              }
              (1 s timeout, daemon thread; never blocks the tool response)
```

Subsequent tools (account lookup, bill resend, etc., to be added) read from
the cache by `X-Conversation-Id` instead of re-querying DPS.

The cache is in-process and replicated per pod. The service is configured
single-replica (`replicas=1` in K8s) until we move to a shared store (Redis).

---

## 5. Common response shapes

**Single match (typical):**

```json
{"found": true, "name": "Stano Muziková"}
```

**Multi-match (rare in prod, observed in test env data pollution):**

```json
{
  "found": true,
  "multiple_matches": true,
  "names": ["Tester AT NECHYTAT", "dgd gd"],
  "message": "Pre toto rodné číslo som našla viacero záznamov. Bude potrebné si vyžiadať dodatočné údaje."
}
```

**Not found:**

```json
{"found": false, "error": "not_found", "message": "Zákazníka s týmto rodným číslom sa nepodarilo nájsť."}
```

**Invalid input (validation failure, no API call made):**

```json
{"found": false, "error": "invalid_input", "message": "Rodné číslo nie je v správnom tvare. Zadajte ho ako 9 alebo 10 cifier bez lomky."}
```

**Upstream failure** (auth, timeout, network, 5xx, malformed body) — all four
upstream error codes share one customer-facing message:

```json
{"found": false, "error": "auth_failed", "message": "Vyskytol sa technický problém. Prepojím vás na operátora."}
```

The error code (`auth_failed`, `upstream_timeout`, `upstream_unreachable`,
`upstream_error`) stays distinct in logs for triage but the customer hears
the same line in every case.

---

## 6. Security posture

### 6.1 PII surface

| Asset                      | Location            | Treatment                                                                |
| -------------------------- | ------------------- | ------------------------------------------------------------------------ |
| Caller input (RČ, OP, …)   | Tool argument       | Validated; never logged in full; only `last4` appears in INFO logs.      |
| Bearer token (`APP_DPS_BEARER_TOKEN`) | Env var      | `pydantic.Field(exclude=True)` ⇒ never appears in `repr(config)` or any log. |
| Authorization header       | HTTPS to DPS only   | Composed at request time; never logged.                                  |
| Party / Customer record    | TTL cache (30 min)  | In-process memory, keyed by `X-Conversation-Id`. Cleared on TTL.         |
| Customer name              | Response to LLM     | The only PII field returned. The LLM consumes it to speak to the caller. |
| `Identification.socialSecurityNumber` from DPS response | Dropped at normalization | Never echoed back in the response. The caller already provided it as input. |
| Logs                       | stdout + Logstash   | INFO records carry `application`, `conversation_id`, `interaction_id`, `trace_id`, `rc_last4`. No raw PII. |

### 6.2 Transport security

- **DPS gateway TLS:** the test environment (`teai.st.sk:8243`) presents a
  self-signed cert. The service runs with `APP_DPS_VERIFY_TLS=false`. **This
  must be flipped (or a CA bundle injected) before any production deploy.**
  Tracked as a pre-prod checklist item in the README.
- **MCP server TLS:** terminated at the K8s ingress. The service itself listens
  on plain HTTP behind it (consistent with other services in this repo).

### 6.3 AuthN/AuthZ

- **To DPS:** static bearer token in env. No rotation. Validation is the
  responsibility of the gateway. **Token rotation policy to be confirmed with
  DPS owners** (tracked in OPEN_QUESTIONS Q2).
- **To MCP server:** off by default in local (`APP_MCP_AUTH_ENABLED=false`).
  In production the agent calls behind an ingress that enforces auth.

### 6.4 Logging & observability

- Structured JSON logs (`APP_JSON_FORMAT_LOGS=true`), shipped to Kibana via
  Logstash (`APP_LOGSTASH_ENABLED=true`).
- Every record carries the MCP correlation IDs (`conversation_id`,
  `interaction_id`, `trace_id`) populated from request headers by the ASGI
  tracing middleware — pivot-able to find a full conversation trace from any
  single log line.
- Error responses preserve the original error code (`auth_failed`,
  `upstream_timeout`, …) in logs even though the customer-facing message is
  unified. Lets ops triage upstream issues quickly.

### 6.5 PII gradient on the NLP-engine push

The cache holds the full identification value (process-local, 30 min TTL).
The NLP-engine PUT is selective:

| Method                                       | NLP `identification` value |
| -------------------------------------------- | -------------------------- |
| `ico` (organization registry number)         | full value, e.g. `86316923`            |
| `kod_zakaznika` (customer ID / billing ref)  | full value, e.g. `1002203204`          |
| `telefon` (MSISDN, intl form)                | full value, e.g. `421902804660`        |
| `seriove_cislo` (device serial)              | full value, e.g. `M91450EB0603`        |
| `rodne_cislo`                                | marker only: `last4=9467`              |
| `op` (občiansky preukaz)                     | marker only: `last4=2148`              |
| `pas` (passport)                             | marker only: `last4=4151`              |

PII identifiers (RČ, OP, passport) never leave the identity service in plain
text via the NLP channel. The downstream NLP/agent layer learns *that*
identification happened (and the method) but not the raw value — it still has
access to `name` via the MCP response, which is the only PII surfaced to the
LLM by design.

---

## 7. Configuration

Required env vars (all prefixed `APP_`; secrets carry `pydantic.Field(exclude=True)`):

| Var                       | Default                                       | Notes                                                      |
| ------------------------- | --------------------------------------------- | ---------------------------------------------------------- |
| `APP_DPS_BASE_URL`        | `https://teai.st.sk:8243/omni/test1`         | DPS gateway root.                                           |
| `APP_DPS_BEARER_TOKEN`    | (empty)                                       | **Required.** Static bearer token. Masked in logs.          |
| `APP_DPS_TIMEOUT_SECONDS` | `10`                                          | Per-request HTTP timeout.                                   |
| `APP_DPS_VERIFY_TLS`      | `false`                                       | Test env has self-signed cert. **Flip for production.**     |
| `APP_DPS_MAX_CANDIDATES`  | `10`                                          | Cap on Party records before the customer-management fanout. |
| `APP_GOODBOT_URL`         | `http://goodbot.internal-test.svc.cluster.local:8121` | NLP engine base URL for state updates. |

---

## 8. Verification status

- **Unit tests:** 124 tests, all green (`pytest -m unit`). Includes test cases
  for each tool's happy path, format validation, format normalization,
  cache writes, all five DPS error → unified-message mappings, and the multi-match
  shape.
- **Live smoke (against test DPS env, with VPN):** all six tools verified for
  the cases listed in §3, plus invalid input + not-found cases.
- **lint / typecheck:** `ruff check` and `basedpyright` clean.

A dedicated `mcp-tester` browser GUI (separate repository) is wired up for
manual ad-hoc testing during owner walkthroughs — see the service README.

---

## 9. Open assumptions / requests for owners

These are the items where we'd like an explicit "yes, that's the right way"
or a correction before going to production. They are also tracked in
[docs/OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

### To Party Management owner (Adam Zverka)

- Filtering Party records: we accept `status ∈ {initialized, validated, null}`
  and drop `{deceased, closed}`. Confirm this is the right semantic.
- Test environment returned 906 records (453 Party + 453 ContactParty) for a
  single `socialSecurityNumber=8753189467`. Confirm this is test pollution,
  not expected production behaviour.
- `subjectRegistrationId` is interpreted as Slovak IČO. Confirm this mapping
  is stable.

### To Customer Management owner (Marián Žákovic)

- `GET /customers?id=<unknown value>` returns the first page of all customers
  (59 KB observed) instead of an empty list. Is the filter ignored when the
  value doesn't match? We avoid this code path; flagging it as a footgun.
- `Customer.name` for B2C uses the `"Surname,FirstName"` format. Confirm this
  is the canonical storage and the only B2C name field worth reading.
- Customer ID with trailing `0` vs. Billing Account ID with trailing `1–9` —
  is this a formal contract or just observed in our test data?
- `billingAccounts?customer.id=<X>` returned `HTTP 501` — confirm this
  parameter is deprecated and we should always go through the nested
  `customerAccounts[].billingAccounts[]` path or a direct `/billingAccounts/{id}` lookup.

### To Product Inventory owner (Adam Babik / Jakub Bednarik)

- `?phoneNumber=...` top-level filter returns `400 "Query parameter is required"`
  unless `query=...` is also set. Confirm intended behaviour.
- `query=publicIdentifier==<msisdn>` is our chosen path for MSISDN lookup
  (international format, `421...`). Confirm this is stable and supported.
- `query=customer==<id>` returns `500 "Cannot compare CustomerRefEntity with String"`.
  We use `customer.id==<id>` which works. Confirm.
- RQL on `phoneNumber` ⇒ `400 "Could not resolve attribute 'phoneNumber'"`.
  So `phoneNumber` is a top-level filter only, not RQL — confirm.
- `query=productSerialNumber==<sn>` is case-sensitive (live probe shows
  lowercase serial returns empty). Confirm intended and stable, and confirm
  `productSerialNumber` is populated for all device types we care about
  (Magio Box / HAG / router / modem / SIM).

### To Security

- The TTL cache (`_IDENTITY_STATE`, 30 min, process-local, keyed by
  conversation ID) holds the full Party+Customer subset including contacts.
  Is 30 min appropriate? Should we use a shared store with explicit eviction
  on call-end?
- Is `{found, name}` the right level of disclosure for the LLM, or should
  even the name be kept opaque (e.g. `{found: true, customer_ref: "<token>"}`)?
- `APP_DPS_VERIFY_TLS=false` default — confirm acceptable for non-prod, and
  what's the right approach for prod (CA bundle vs. service-mesh TLS).
- Static bearer token — confirm rotation policy.
