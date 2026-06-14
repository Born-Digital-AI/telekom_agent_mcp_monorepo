# mcp_telekom_identity

MCP server for identifying (and later authenticating) Slovak Telekom customers
against the DPS API (party-management + customer-management).

## Cache & NLP state

After every **successful** identification, the tool:

1. Caches the result in a 30-minute TTL store keyed by `X-Conversation-Id`:

   ```json
   {
     "identification_method": "kod_zakaznika",
     "identification_value": "1002203204",
     "rc_last4": "3204",
     "candidates": [...]
   }
   ```

2. Fires a fire-and-forget PUT to the NLP engine (`APP_GOODBOT_URL`) with a
   `named_entities` state update:

   - **Non-PII methods** (`ico`, `kod_zakaznika`, `telefon`, `seriove_cislo`):
     `{"identification_method": "telefon", "identification": "421902804660"}`
   - **PII methods** (`rodne_cislo`, `op`, `pas`):
     `{"identification_method": "rodne_cislo", "identification": "last4=9467"}` —
     only the last 4 characters are sent so the LLM/NLP layer knows
     identification happened without seeing the raw value.

Validation failures and `not_found` do **not** push to NLP. The push runs on a
daemon thread with a 1-second timeout; a slow or down NLP engine never affects
tool response latency.

## Authentication

Once the customer is identified, `autentifikacia` runs a multi-step factor check.
Two levels:

- **`standard`** — 2 factors needed (e.g. invoice resend)
- **`sensitive`** — 3 factors needed (e.g. password change, billing change)

The auth type comes from NLP `named_entities.authentication_type`. Default if
absent is `standard`.

### Factor order

The tool always asks in order 1 → 2 → 3 → 4. The LLM/agent may skip the
current factor (if the customer doesn't have that data) by calling
`autentifikacia(skip_current_factor=true)`.

| # | Factor           | Source                                            | How verified                                                                                      |
| - | ---------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 1 | `trusted_source` | NLP `input_source` (caller-ID phone / from email) | match against the identified party's `contacts` (mobile or email)                                 |
| 2 | `name`           | customer says (LLM passes via `meno_priezvisko=`) | lenient compare against `candidate.name`: case-insensitive, strip diacritics, token order ignored |
| 3 | `kod_adresata`   | customer reads off invoice (`kod_adresata=`)      | exact match against `candidate.billing_account_ids[]`                                             |
| 4 | `rc_last4`       | customer says (`rc_last4=`)                       | match against last 4 of Party's `socialSecurityNumber` identification                             |

### Auto-credit from identification

| Identification method                                                  | Factor auto-credited                         |
| ---------------------------------------------------------------------- | -------------------------------------------- |
| `identifikacia_rodne_cislo`                                            | **4** (caller already proved RČ knowledge)   |
| `identifikacia_kod_zakaznika`, ends `1–9` (billing account)            | **3** (billing account = kod adresáta)       |
| `identifikacia_kod_zakaznika`, ends `0` (customer id)                  | none                                         |
| `identifikacia_op` / `_pas` / `_ico` / `_telefon` / `_seriove_cislo`  | none                                          |

Factor 1 (trusted source) is **always** re-evaluated on each call against
the current `input_source` from the NLP mirror — it can credit later if
NLP arrives late.

### Lazy Party fetch for `rc_last4`

Identification tools 1–4 (Party-based) extract and cache `auth_rc_last4`
during identification. Tools 5–7 (`kod_zakaznika`, `telefon`, `seriove_cislo`)
don't fetch the Party — they only have the Customer. When the auth tool
needs factor 4 in those cases, it lazy-fetches the Party via
`GET /party-management/3.54.0/v2/parties/{id}` using the cached `party_id`.
This keeps identification fast and only pays the extra request when factor 4
is actually asked.

### Response shapes

**Need next factor:**

```json
{
  "authenticated": false,
  "level_required": "standard",
  "factors_satisfied": ["trusted_source"],
  "factors_remaining": 1,
  "next_factor": "name",
  "suggested_response": "Pre overenie totožnosti mi povedzte vaše meno a priezvisko.",
  "instruction": "Počkaj na odpoveď zákazníka a zavolaj autentifikacia s parametrom meno_priezvisko=<odpoveď>. Ak zákazník daný údaj nemá, zavolaj autentifikacia(skip_current_factor=True)."
}
```

**Success:**

```json
{
  "authenticated": true,
  "level": "standard",
  "factors_satisfied": ["trusted_source", "name"],
  "suggested_response": "Ďakujem, overenie prebehlo úspešne. S čím vám môžem pomôcť?"
}
```

**Wrong value (attempts remaining):**

```json
{
  "authenticated": false,
  "factor_failed": "name",
  "attempts_remaining": 2,
  "suggested_response": "Tento údaj sa nezhoduje. Skúste, prosím, znova. Zostávajú vám 2 pokusy.",
  "instruction": "..."
}
```

**Other errors:** `identification_required`, `out_of_order` (with `expected_factor`),
`multiple_factors_in_call`, `ambiguous_identification`, `cannot_authenticate`,
`missing_conversation_id`.

### Test scenarios (verified live)

| Sequence                                                                                                   | Outcome                                                    |
| ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `identifikacia_rodne_cislo("7304292105")` → `autentifikacia(meno_priezvisko="Stano Muzikova")`             | standard ✓ (factors: name + rc_last4 auto)                 |
| `identifikacia_rodne_cislo` → `nastav_test_kontext(authentication_type="sensitive")` → meno → kod_adresata | sensitive ✓ (factors: name + kod_adresata + rc_last4 auto) |
| `identifikacia_kod_zakaznika("1002203204")` (billing acc) → meno                                           | standard ✓ (factors: kod_adresata auto + name)             |
| `identifikacia_kod_zakaznika("1002203200")` (customer id) → meno → kod_adresata                            | standard ✓                                                 |
| `identifikacia_telefon("0902804660")` → name → kod_adresata → rc_last4 (sensitive)                         | sensitive ✓ (lazy Party fetch for rc_last4)                |
| No identification → `autentifikacia()`                                                                     | `identification_required`                                  |
| 3× wrong name                                                                                              | `factors_failed[name]`, next factor advances               |

### Test/debug tool: `nastav_test_kontext`

Sets `input_source` and/or `authentication_type` in the local NLP mirror cache
— used in tests and `mcp-tester` while the read path from the real NLP engine
is not yet wired (tracked in [docs/OPEN_QUESTIONS.md](../../docs/OPEN_QUESTIONS.md)).
In production this tool should be removed or restricted; values arrive from NLP.

```python
nastav_test_kontext(
  input_source="0902555002",     # simulates caller-ID phone (factor 1 source)
  authentication_type="sensitive" # default is "standard"
)
```

## Known test customers (DPS test env)

Pick a row, then paste any of the values into the matching tool input in mcp-tester.
Empty cell = that customer has no value of that kind in DPS.

| Customer | `rodne_cislo` | `cislo_op` | `cislo_pasu` | `ico` | `kod_zakaznika` (customer id) | `kod_zakaznika` / `kod_adresata` (billing acc) | `telefon` | `seriove_cislo` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Stano Muziková | `7304292105` | `RC932733` | — | — | `1002203200` | `1002203204`, `1002203202` | `0902804660`, `0996650543` | `M91450EB0603`, `K5D0M374LXO`, `J252BS000119` |
| Valent Dorcak | `8407160630` | `HY258342` | — | — | `4103349400` | `4103349402`, `4103349401` | — | — |
| Imre Mlynarcik | `7210055589` | — | `BR154151` | — | `1138860700` | `1138860703` | — | — |
| Libusa Sotakova | `6862147292` | — | — | — | `1200456600` | `1200456601` | — | — |
| Tester AT NECHYTAT _(multi)_ | `8753189467` | `MM852148` | — | — | `4482259100` | — | `0902555002` | — |
| Rmc S.R.O. _(organization)_ | — | — | — | `86316923` | — | — | — | — |
| A.B.Zrtv _(B2B)_ | — | — | — | — | `4059299000` | `4059299001` | — | — |
| Creditinfo Slovakia, S.R.O. _(B2B)_ | — | — | — | — | `2300000400` | `2300000404`, `2300000401`, `2300000405`, `2300000406` | — | — |
| J A L & Š, S. R. O. _(B2B)_ | — | — | — | — | `4108064300` | `4108064301`, `4108064302` | — | — |
| Stano Majchrak | — | — | — | — | `4002141300` | `4002141301`, `4002141304` | `0928101901`† | — |
| Stano Rehák | — | — | — | — | `2315054900` | `2315054901` | `0975277031`† | — |
| Vitaliy Turzová | — | — | — | — | `2315057500` | `2315057501` | `0905555711`† | — |
| `Vrbova,Konštantín,Ing.` _(multi-comma)_ | — | — | — | — | `2315059400` | `2315059402`, `2315059403`, `2315059404`, `2315059405`, `2315059406`, `2315059408` | `0908554490`† | — |
| Jindrich Piacek | — | — | — | — | `2315055000` | `2315055002`, `2315055003` | `0948346170`† | — |
| Jarolím Záhorská | — | — | — | — | `2315053900` | `2315053902` | `0951101462`† | — |
| `Biely,Lubomir,Ing.` _(multi-comma)_ | — | — | — | — | `4002130900` | `4002130901`, `4002130903`, `4002130904` | `0948192661`† | — |
| Valent Turčanová | — | — | — | — | `4002152400` | `4002152401`, `4002152402` | `0968333256`† | — |
| Justina Fridrichova | — | — | — | — | `4002187300` | `4002187301`, `4002187302` | `0937888267`† | — |
| Julian Nedelka | — | — | — | — | `2315075000` | `2315075001`, `2315075003`, `2315075004` | `0913092013`† | — |
| Dusan Nad | — | — | — | — | `2315419800` | `2315419801` | `0957643790`† | — |
| Pool Controls Slovakia, Spol. S _(B2B)_ | — | — | — | — | `3107175000` | `3107175004` | `0978153667`† | — |
| Grade/Tbwa, S.R.O. _(B2B)_ | — | — | — | — | `4002814700` | `4002814701` | `0973600491`† | — |

Notes:

- `kod_zakaznika` ending in **`0`** is the Customer ID; ending in **`1–9`** is a Billing Account ID (also the value to use as `kod_adresata` in `autentifikacia`).
- "Tester AT NECHYTAT" RČ and OP return **multi-match** because the DPS test environment contains ~450 duplicate records under that identifier.
- All `telefon` values can be entered in SK local form (`0902…`), with `+` (`+421…`), or without (`421…`); the tool normalizes.
- All `seriove_cislo` values are case-insensitive and ignore spaces / dashes / dots / slashes.
- †  Phone from billing SMS notification — **not verified** as a Product Inventory MSISDN; `identifikacia_telefon` may not find these customers.
- _(multi-comma)_ DPS name has >1 comma (e.g. `Vrbova,Konštantín,Ing.`). The tool reverses B2C names only when there is exactly 1 comma — these are returned raw. Tool output will show the literal DPS string.

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

### `identifikacia_telefon(telefon)` — Telefónne číslo (MSISDN)

| Parameter | Format                                                              |
| --------- | ------------------------------------------------------------------- |
| `telefon` | SK local (`0904...`) or international (`+421904...` / `421904...`) |

The tool normalizes the input to international format `421XXXXXXXXX` (12 digits, no `+`),
then queries Product Inventory `GET /products?query=publicIdentifier==<msisdn>`. The
matched product carries `customer.id` which is resolved via the same path used by
`identifikacia_kod_zakaznika`.

### `identifikacia_seriove_cislo(seriove_cislo)` — Sériové číslo zariadenia

| Parameter         | Format                                                |
| ----------------- | ----------------------------------------------------- |
| `seriove_cislo`   | 8–30 alphanumeric characters (e.g. `M91450EB0603`)    |

Suited for the customer reading the serial off a router, set-top box, modem
or similar device. The tool strips spaces, dashes, slashes and dots from the
input and uppercases letters before the lookup, then queries Product Inventory
via `GET /products?query=productSerialNumber==<sn>`. The matched product
carries `customer.id` which is resolved through customer-management. The
backing field is case-sensitive in DPS — normalization is the responsibility
of the tool.

## Environment variables

| Var | Default | Notes |
| --- | --- | --- |
| `APP_DPS_BASE_URL` | `https://teai.st.sk:8243/omni/test1` | DPS root URL |
| `APP_DPS_BEARER_TOKEN` | _(empty)_ | Required. Static bearer token. |
| `APP_DPS_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `APP_DPS_VERIFY_TLS` | `false` | Set `true` once a proper CA chain is wired |
| `APP_DPS_MAX_CANDIDATES` | `10` | Cap on Party records before customer fanout |
| `APP_GOODBOT_URL` | `http://goodbot.internal-test.svc.cluster.local:8121` | NLP engine base URL for state updates |

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
| `4002141300` | Customer ID (B2C)          | `{found, name: "Stano Majchrak"}`                             |
| `2315054900` | Customer ID (B2C)          | `{found, name: "Stano Rehák"}`                                |
| `2315057500` | Customer ID (B2C)          | `{found, name: "Vitaliy Turzová"}`                            |
| `2315059400` | Customer ID (B2C, multi-comma) | `{found, name: "Vrbova,Konštantín,Ing."}`                 |
| `2315055000` | Customer ID (B2C)          | `{found, name: "Jindrich Piacek"}`                            |
| `2315053900` | Customer ID (B2C)          | `{found, name: "Jarolím Záhorská"}`                           |
| `4002130900` | Customer ID (B2C, multi-comma) | `{found, name: "Biely,Lubomir,Ing."}`                     |
| `4002152400` | Customer ID (B2C)          | `{found, name: "Valent Turčanová"}`                           |
| `4002187300` | Customer ID (B2C)          | `{found, name: "Justina Fridrichova"}`                        |
| `2315075000` | Customer ID (B2C)          | `{found, name: "Julian Nedelka"}`                             |
| `2315419800` | Customer ID (B2C)          | `{found, name: "Dusan Nad"}`                                  |
| `3107175000` | Customer ID (B2B)          | `{found, name: "Pool Controls Slovakia, Spol. S "}`           |
| `4002814700` | Customer ID (B2B)          | `{found, name: "Grade/Tbwa, S.R.O."}`                         |

### `identifikacia_telefon`

| Input              | Normalized       | Expected                                                |
| ------------------ | ---------------- | ------------------------------------------------------- |
| `0902804660`       | `421902804660`   | `{found, name: "Stano Muziková"}` (PARTY_1002203200)    |
| `+421902804660`    | `421902804660`   | same as above                                           |
| `421902804660`     | `421902804660`   | same as above                                           |
| `0902 804 660`     | `421902804660`   | same as above (whitespace stripped)                     |
| `0000000000`       | `421000000000`   | `{found: false, error: "not_found"}`                    |
| `abc`, `+abc`, ` ` | —                | `{found: false, error: "invalid_input"}`                |

### `identifikacia_seriove_cislo`

| Input              | Normalized       | Expected                                                                |
| ------------------ | ---------------- | ----------------------------------------------------------------------- |
| `M91450EB0603`     | `M91450EB0603`   | `{found, name: "Stano Muziková"}` (Magio Box s HDD, customer 1002203200) |
| `K5D0M374LXO`      | `K5D0M374LXO`    | `{found, name: "Stano Muziková"}` (Magio Box bez HDD)                    |
| `J252BS000119`     | `J252BS000119`   | `{found, name: "Stano Muziková"}` (HAG)                                  |
| `m91450eb0603`     | `M91450EB0603`   | same as first row (lowercase normalized)                                |
| `M9145-0EB-0603`   | `M91450EB0603`   | same (hyphens stripped)                                                 |
| `UNKNOWNSN001`     | `UNKNOWNSN001`   | `{found: false, error: "not_found"}`                                    |
| `abc`, `#`, ` `    | —                | `{found: false, error: "invalid_input"}`                                |
