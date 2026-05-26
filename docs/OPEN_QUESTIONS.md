# Open Questions / Blockers

Tracker for unanswered questions and blockers across the `mcp_telekom_*` services
that require input from business or technical owners. Each entry follows the same
shape so it's easy to scan and act on.

## Convention

Each question gets its own `## Q<N>` heading. Body fields:

- **Status:** 🔴 Open · 🟡 Waiting on someone · 🟢 Answered
- **Raised:** ISO date
- **Service:** which `mcp_telekom_*` is affected
- **Context:** what we wanted to do
- **What we tried:** concrete probes + outcomes (curl + HTTP code + body excerpt)
- **Who to ask:** name + role (link to email if known)
- **Unblocks:** the deliverable we need to close this (a swagger, an answer, a credential, …)
- **Workaround in place:** what we do today while waiting

---

## Q1: How to look up a customer by MSISDN (telefónne číslo)?  🟢 Answered

**Raised:** 2026-05-22 · **Resolved:** 2026-05-22
**Service:** `mcp_telekom_identity`

### Resolution

The Product Inventory API (`/product-inventory/4.64/products`) supports MSISDN lookup
via RQL on `publicIdentifier`. Mobile tariff products are stored with the MSISDN as their
`publicIdentifier` in **international format without `+`** (e.g. `421902804660`).

**Working query:**

```http
GET /omni/test1/product-inventory/4.64/products?query=publicIdentifier==421902804660&fields=*
```

Returns a list of `Product` objects, each carrying `customer: {id}` that links back to
customer-management. From there we reuse the existing `get_customer_by_id` to build the
candidate (same flow as `identifikacia_kod_zakaznika` from the billing-account branch).

**MSISDN format normalization** required before the query:

- `0902804660` (SK local) → strip leading `0`, prepend `421` → `421902804660`
- `+421902804660` → strip leading `+` → `421902804660`
- `421902804660` (already intl) → as-is
- `00421902804660` → strip leading `00` → `421902804660`

Search with the local format (`0902...`) returns an empty list — no exception, just no
match. Validation must enforce international form before the call.

**Implemented as** `identifikacia_telefon(telefon)` in `mcp_telekom_identity` (see commit
on PR #2).

---

### Original investigation (preserved for context)

### Context

The voice agent on the customer-care line knows the caller's MSISDN from caller-ID.
It should be the **primary** identifier — RČ / OP / pas / IČO / kód zákazníka are
fallbacks when no MSISDN is available (e.g. hidden number, different SIM, missed call
follow-up). Without MSISDN search the tool set has a real gap.

### What we tried (against `https://teai.st.sk:8243/omni/test1/`)

| Approach | Endpoint | Outcome |
| --- | --- | --- |
| `identificationType=msisdn` | `party-management/3.54.0/v2/parties` | `HTTP 200 []` — endpoint accepts the param but never matches |
| `identificationType=phoneNumber` / `mobile` / `mobileNumber` | party-mgmt | `HTTP 200 []` — same |
| `customerAccountId=<msisdn>` | party-mgmt | `HTTP 200 []` |
| RQL `query=contacts.medium.number==<msisdn>` (3 syntax variants: `==`, `=eq=`, `=`) | party-mgmt | **timeout > 20s** — likely full table scan or unsupported |
| Same RQL variants | `customer-management/4.67.0/customers` | **timeout > 10s** |
| `?id=<msisdn>` | customer-mgmt | `HTTP 200` but returned **first page of all customers (59 KB)** — the filter is silently ignored when the value doesn't match a known customer ID. ⚠️ Unsafe to use this way. |
| Probe other TMF namespaces (`service-management`, `service-inventory`, `subscription-management`, `product-inventory`, `gsm-administration`) on the same gateway | various paths/versions | `HTTP 404` — none exposed |
| Direct fetch of a known Party record | `party-mgmt /v2/parties/PARTY_1002203200` | `HTTP 200` — confirms `Party.contacts[type=mobile].medium.number = "0996650543"` is **present** in the data, but cannot be queried as a filter |
| Customer-level `Customer.contacts` array on a fetched customer | customer-mgmt `/customers/{id}` | **Empty `[]`** for individuals. Only `billingAccount.contacts` is populated and it holds only `address`, no `mobile`. |

Test data used:

- `PARTY_1002203200` (Stano Muziková) → `Party.contacts[mobile] = 0996650543`
- `PARTY_4482259100` (Tester AT NECHYTAT) → `Party.contacts[mobile] = 0902555002`

Conclusion: **MSISDN is stored on `Party.contacts[type=mobile].medium.number` but is
not indexed for query in either party-management or customer-management.** No other
service namespace is exposed on the gateway with our current token.

### Who to ask

- **Business owner** (both swaggers): [Peter Furucz](mailto:peter.furucz@external.telekom.sk)
- **Technical owner — party-management:** [Adam Zverka](mailto:adam.zverka@telekom.sk)
- **Technical owner — customer-management:** Marián Žákovic (per swagger contact)
- **General contact:** [omni.st.cit@telekom.sk](mailto:omni.st.cit@telekom.sk)

### Unblocks

Any of:

1. **Swagger of the right DPS API.** Most likely candidates:
   - TMF638 Service Inventory (`/service-inventory`)
   - TMF634 Resource Inventory (`/resource-inventory`)
   - A Slovak Telekom custom endpoint (GSM administration / subscriber lookup)
2. **Confirmation that RQL `contacts.medium.number==X` is intended to work** on
   party-management, and a hint on how to avoid the full-table scan (e.g. a required
   pagination parameter, a different field path, …).
3. **A dedicated `identificationType` value** that maps to MSISDN in DPS (which would
   need to be added to the official enum and indexed server-side).

### Workaround in place

None. The tool `identifikacia_telefon` does not exist yet. The MCP server falls back
to the other four identification tools (`identifikacia_rodne_cislo`, `identifikacia_op`,
`identifikacia_pas`, `identifikacia_ico`, `identifikacia_kod_zakaznika`).

A possible **stub-only** tool that responds with `not_supported_yet` was considered
but deferred — surfacing a non-functional tool to the LLM mainly creates confusion.

---

## Q2: Walk-through of the full identification flow with DPS API owners + Security  🟡 Waiting

**Raised:** 2026-05-22
**Service:** `mcp_telekom_identity`

### Context (Q2)

The identification toolset now spans **three DPS APIs** (party-management, customer-management,
product-inventory) with **six tools** and several edge cases (B2C name reversal, MSISDN
international normalization, customer-vs-billing-account dispatch by trailing digit, status
filtering on Party records, IČO via `subjectRegistrationId`). Several of these were derived
by live probing rather than from formal documentation — they work today but the
"approved-by-owner" stamp is missing.

### What needs to happen before this can be considered production-ready

1. **DPS API ownership review** — walk each tool through the relevant API owner and confirm:
   - Endpoint + query parameter choice is the intended way to look up by that field
     (not a side effect that may disappear).
   - Rate limits / throttling expectations.
   - That returning >450 duplicate Party records for one socialSecurityNumber (test env) is
     not expected in production.
   - That `verify_tls=false` is acceptable for the test environment and that a CA-trusted
     cert exists for production.
   - That the static bearer token approach is right and what the rotation policy is.

2. **Security review** of the end-to-end flow, covering:
   - PII handling (what's logged, what's cached, where, for how long).
   - The `_IDENTITY_STATE` in-memory TTL cache (30 min, keyed by `conversation_id`).
   - The `customer-management ?id=<invalid value>` behaviour that returned the full
     customer page (59 KB) — we don't use this path but it's a footgun worth flagging.
   - Whether the response shape (`{found, name}`) is the right amount of disclosure for a
     conversational AI agent in a customer-care line.

### Who to ask (Q2)

- **Party Management** (party-management/3.54.0) — Adam Zverka, [adam.zverka@telekom.sk](mailto:adam.zverka@telekom.sk)
- **Customer Management** (customer-management/4.67.0) — Marián Žákovic (per swagger contact)
- **Product Inventory** (product-inventory/4.64) — Adam Babik (tech), [adam.babik@telekom.sk](mailto:adam.babik@telekom.sk); Jakub Bednarik (business), [jakub.bednarik@telekom.sk](mailto:jakub.bednarik@telekom.sk)
- **Cross-API business owner** — Peter Furucz, [peter.furucz@external.telekom.sk](mailto:peter.furucz@external.telekom.sk)
- **General contact** — [omni.st.cit@telekom.sk](mailto:omni.st.cit@telekom.sk)
- **Security** — BD security contact (TBD)

### Unblocks (Q2)

- A scheduled walk-through (30–60 min) with the four DPS owners + security.
- The review document at [docs/identification-review.md](identification-review.md) maintained
  alongside the code, sent ahead of the meeting.

### Workaround in place (Q2)

The tools work and are live-verified against the test DPS environment, with 124 unit
tests. README has full test scenarios. But:

- `APP_DPS_VERIFY_TLS=false` is the default — must be flipped before any production deploy.
- The bearer token is static and lives in env — no rotation.
- Q1 (MSISDN) was resolved by live probing, not by owner confirmation — could break if DPS
  changes Product.publicIdentifier semantics.

---

## Q3: Wire real NLP `GET /named_entities` read path  🟡 Waiting

**Raised:** 2026-05-26
**Service:** `mcp_telekom_identity`

### Context (Q3)

The auth flow needs `input_source` (caller phone / email from caller-ID or
email headers, used to score factor 1 "trusted source") and
`authentication_type` (`standard` | `sensitive`, used to decide if 2 or 3
factors are needed). Both are _produced_ by the NLP engine and consumed by
our auth tool.

Today the tool reads these from `_NLP_MIRROR_STATE`, a process-local TTL
cache that:

1. Mirrors every `_nlp_set_state` PUT we make (so our own writes round-trip).
2. Is externally populated only by the debug tool `nastav_test_kontext`,
   which we ship for test / `mcp-tester` flows.

This is fine for development but in production the NLP engine is the source
of truth. We need to fetch named entities from it on each auth call:

```http
GET <APP_GOODBOT_URL>/conversations/<conversation_id>/named_entities
```

### What needs to happen (Q3)

1. Add a client (or inline `urllib`) for the GET endpoint with same timeout
   semantics as `_nlp_set_state` (1 s aggressive cap, daemon-thread is fine if
   we cache + use stale value on timeout).
2. Change `_nlp_get_named_entities` to: try the GET first, fall back to mirror.
3. Decide on debug-tool fate: either gate `nastav_test_kontext` behind an env
   flag (e.g. only when `APP_NLP_MIRROR_ONLY=true`) or remove its registration
   in production deployments entirely.
4. Update `docs/identification-review.md` §6.6 (PII gradient) once the read
   contract is confirmed with NLP team.

### Who to ask (Q3)

- **NLP engine owner** — confirm the exact `GET /named_entities` path,
  response shape, and what keys can be expected to be populated when the
  conversation hits the auth tool (specifically `input_source` and
  `authentication_type`).

### Unblocks (Q3)

- A documented `GET /conversations/{id}/named_entities` endpoint on goodbot.
- Decision on whether `nastav_test_kontext` stays as a controlled debug tool
  or is removed.

### Workaround in place (Q3)

`_NLP_MIRROR_STATE` + `nastav_test_kontext`. The mirror is populated by our
own `_nlp_set_state` writes AND any external write via `nastav_test_kontext`.
Production parity is the only thing missing — the logic on top of the mirror
read already exists.
