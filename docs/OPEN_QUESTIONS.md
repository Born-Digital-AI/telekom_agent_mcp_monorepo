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

## Q1: How to look up a customer by MSISDN (telefónne číslo)?  🔴 Open

**Raised:** 2026-05-22
**Service:** `mcp_telekom_identity`

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
