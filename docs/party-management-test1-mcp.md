# Party Management MCP (test1) — pripojenie a použitie

MCP server vystavujúci Party Management API ako nástroje pre Claude Code.
Prostredie: **test1**. Auth: **OAuth cez GitLab** (nie bearer token v env vars).

## Pridanie servera

```bash
claude mcp add --transport http party-management-test1 https://party-management-mcp-test1.tomni.st.sk/mcp
```

Server beží na internej IP — **VPN required**.

### SSL certifikát

`*.tomni.st.sk` používa Slovak Telekom self-signed CA. Pri prvom pridaní je potrebné pridať CA do macOS Keychain (jednorazovo — ak si to urobil pre `customer-management-test1`, preskočiť):

```bash
# Stiahni certifikátový reťazec
openssl s_client -connect party-management-mcp-test1.tomni.st.sk:443 -showcerts </dev/null 2>/dev/null \
  | awk 'BEGIN{c=0; file=""} /BEGIN CERTIFICATE/{c++; file="/tmp/tomni-cert-" c ".pem"} file{print > file} /END CERTIFICATE/{file=""}'

# Pridaj Root CA a Issuing CA s dôverou
security add-trusted-cert -d -r trustRoot   -k ~/Library/Keychains/login.keychain-db /tmp/tomni-cert-3.pem
security add-trusted-cert -d -r trustAsRoot -k ~/Library/Keychains/login.keychain-db /tmp/tomni-cert-2.pem
```

Certifikáty (cert-2 = Issuing CA, cert-3 = Root CA):

- `Slovak Telekom Root CA 2`
- `Slovak Telekom Issuing CA 02 Class B`

Po pridaní CA by mal `claude mcp list` ukazovať `! Needs authentication` (nie `✗ Failed to connect`).

## Autentikácia (OAuth GitLab)

Token platí **8 hodín**. Po expirácii sa flow automaticky zopakuje.

**Povolené domény:** `telekom.sk`, `external.telekom.sk`, `t-systems.com`, `telekom.de`

1. V Claude Code napíš `/mcp`
2. Vyber **`party-management-test1`** a klikni **Authenticate**
3. Otvorí sa browser → GitLab login → Authorize
4. Po potvrdení: `Authentication successful. Connected to party-management-test1.`

> Ak sa server nezobrazí v `/mcp` dialógu po pridaní: **Cmd+Shift+P → Reload Window** vo VS Code.

## Dostupné nástroje

| Tool | Účel |
| --- | --- |
| `search_parties` | Vyhľadanie party podľa `party_id`, alebo `identification_id` + `identification_type` (RČ, OP, pas, IČO, DIČ). Podporuje aj raw RQL cez `query`. |
| `get_party` | Detail party podľa ID — kompletný TMF632 profil |
| `get_party_roles` | Zoznam rolí party (napr. Customer, Contact) |

### Poznámky k `search_parties`

- `party_id` akceptuje `PARTY_1234` aj holé `1234` (server dopíše prefix automaticky)
- `identification_id` + `identification_type` musia byť vždy **spolu**
- V dev/test1 je spoľahlivo funkčný iba lookup cez `party_id` a `identification_id` — RQL query cez iné polia môže vrátiť HTTP 500

## Troubleshooting

| Problém | Riešenie |
| --- | --- |
| `✗ Failed to connect` v `claude mcp list` | SSL CA nie je pridaná — pozri sekciu SSL certifikát vyššie |
| Server sa nezobrazí v `/mcp` dialógu VS Code | `Cmd+Shift+P → Reload Window` |
| `Authentication failed` v browseri | Email nie je v povolenej doméne — požiadaj tím o pridanie do `ALLOWED_EMAIL_DOMAINS` |
| `missing session` pri prvom volaní | Server bol reštartnutý — `/mcp` → re-authenticate |
| `403 FORBIDDEN` | Nie si na VPN |

## Kontakt a repo

Tím: Party Management (AIM tribe)
Repo: `gitlab.services.itc.st.sk/omnichannel-st/party/party-management/mcp`
