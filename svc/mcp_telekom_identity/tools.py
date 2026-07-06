"""MCP tools for mcp_telekom_identity.

This module wires the customer-facing MCP tools (``identifikacia``,
``autentifikacia``, the widget renderers, ``over_viazanost`` and the test helper)
onto the FastMCP registry. The supporting logic lives in focused sibling modules:

- :mod:`.classify`    — identifier normalization / validation / type detection
- :mod:`.nlp_state`   — named_entities sync with the NLP engine
- :mod:`.candidates`  — DPS Party/Customer → ``candidate`` shaping + error payloads
- :mod:`.auth`        — authentication factor logic and checks
- :mod:`.viazanost`   — agreement (viazanosť) parsing and classification
- :mod:`._state`      — the shared per-conversation TTL stores

Names re-exported below (``# noqa: F401``) keep the historical
``tools._helper`` import surface that the test-suite relies on.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from lib.bubble_widgets import bubble_widget_result
from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool
from svc.mcp_telekom_identity import widgets
from svc.mcp_telekom_identity._state import (  # noqa: F401  (re-exported for tests)
    _AUTH_STATE,
    _AUTH_TTL_SECONDS,
    _IDENTITY_STATE,
    _IDENTITY_TTL_SECONDS,
    _NLP_CONSUMED_STATE,
    _NLP_MIRROR_STATE,
    _NLP_MIRROR_TTL_SECONDS,
    _NLP_PENDING_STATE,
)
from svc.mcp_telekom_identity.auth import (
    _AUTH_TYPE_SENSITIVE,
    _AUTH_TYPE_STANDARD,
    _FACTOR_KOD_ADRESATA,
    _FACTOR_NAME,
    _FACTOR_RC_LAST4,
    _FACTOR_TRUSTED_SOURCE,
    _MAX_AUTH_ATTEMPTS_PER_FACTOR,
    _check_kod_adresata,
    _check_name,
    _check_rc_last4,
    _credited_factors_from_identification,
    _instruction_for_factor,
    _need_factor_response,
    _next_factor,
    _peek_next_auth_factor,
    _persist_auth_state,
    _required_factors,
    _suggested_response_for_factor,
)
from svc.mcp_telekom_identity.candidates import (
    _UPSTREAM_ERROR_MESSAGE,
    _candidate,
    _candidate_from_customer,
    _dps_error_payload,
    _extract_rc_last4_from_party,
    _json,
)
from svc.mcp_telekom_identity.classify import (
    _ICO_PATTERN,
    _KOD_PATTERN,
    _RC_PATTERN,
    _TYPE_AUTO,
    _TYPE_ICO,
    _TYPE_KOD_ZAKAZNIKA,
    _TYPE_RODNE_CISLO,
    _TYPE_SERIOVE_CISLO,
    _TYPE_TELEFON,
    _classify_identifier,
    _is_valid_ico,  # noqa: F401  (re-exported for tests)
    _is_valid_rodne_cislo,  # noqa: F401  (re-exported for tests)
    _normalize_msisdn,
    _normalize_serial,
)
from svc.mcp_telekom_identity.dps_get_client import DPSError
from svc.mcp_telekom_identity.nlp_state import (
    _CHANNEL_CHAT,
    _CHANNEL_KEY,
    _consume_named_entity,
    _nlp_flush,
    _nlp_get_named_entities,
    _nlp_load,
    _nlp_set_state,
)
from svc.mcp_telekom_identity.viazanost import (
    _VIAZANOST_TYP_ORDER,
    _classify_viazanost,
    _parse_agreement_date,
    _parse_products_with_active_agreements,
)

if TYPE_CHECKING:
    from svc.mcp_telekom_identity.dps_get_client import DPSGetClient

_log = logging.getLogger(__name__)


# --- User-facing messages + tool descriptions --------------------------------

_RC_INVALID_MESSAGE = (
    "Rodné číslo nie je v správnom tvare. Zadajte ho ako 9 alebo 10 cifier bez lomky."
)
_RC_NOT_FOUND_MESSAGE = "Zákazníka s týmto rodným číslom sa nepodarilo nájsť."

_ICO_INVALID_MESSAGE = "IČO nie je v správnom tvare. Zadajte ho ako 8 cifier."
_ICO_NOT_FOUND_MESSAGE = "Spoločnosť s týmto IČO sa nepodarilo nájsť."

_KOD_INVALID_MESSAGE = "Kód zákazníka má tvar 8 až 12 cifier (napr. 4482259100)."
_KOD_NOT_FOUND_MESSAGE = "Zákazníka s týmto kódom sa nepodarilo nájsť."

_TEL_INVALID_MESSAGE = (
    "Telefónne číslo nie je v správnom tvare. Zadajte ho ako 0904... (10 cifier) "
    "alebo +421904... (medzinárodný tvar)."
)
_TEL_NOT_FOUND_MESSAGE = "Zákazníka s týmto telefónnym číslom sa nepodarilo nájsť."

_SERIAL_INVALID_MESSAGE = (
    "Sériové číslo nie je v správnom tvare. Zadajte ho ako 8 až 30 alfanumerických "
    "znakov (napr. M91450EB0603)."
)
_SERIAL_NOT_FOUND_MESSAGE = "Zákazníka s týmto sériovým číslom sa nepodarilo nájsť."

# Single identification entry point — auto-detects the identifier type.
# NOTE: this tool only PROCESSES an identifier; it never renders a widget. To show
# the form use zobraz_identifikacny_widget.
_IDENTIFIKACIA_TOOL_DESCRIPTION = (
    "SPRACUJ identifikačný údaj zákazníka a identifikuj ho "
    "(telefónne číslo, IČO, rodné číslo, kód zákazníka/fakturačného účtu, sériové číslo). "
    "Typ údaja rozpozná tool sám podľa formátu. Po úspechu vráti meno zákazníka a interné "
    "identifikátory si uloží do pamäte konverzácie pre ďalšie nástroje.\n"
    "KEDY volať: (1) keď už hodnotu máš → zavolaj s hodnota=<údaj>; (2) keď zákazník odoslal "
    "identifikačný widget (utterance 'identifikacia_widget_submitted') → zavolaj BEZ parametrov, "
    "hodnotu si prečíta z pamäte konverzácie. "
    "Na ZOBRAZENIE formulára tento tool NEVOLAJ — na to slúži zobraz_identifikacny_widget."
)

_IDENT_INPUT_REQUIRED_MESSAGE = (
    "Potrebujem identifikačný údaj — napríklad telefónne číslo, rodné číslo, "
    "IČO, kód zákazníka alebo sériové číslo zariadenia."
)
_IDENT_UNRECOGNIZED_MESSAGE = (
    "Tento údaj sa mi nepodarilo rozpoznať. Skúste, prosím, telefónne číslo, rodné číslo, "
    "IČO, kód zákazníka alebo sériové číslo zariadenia."
)
_IDENT_AMBIGUOUS_MESSAGE = (
    "Zadaný údaj sa dal rozpoznať viacerými spôsobmi — potrebujem, aby zákazník vybral typ."
)

_IDENT_WIDGET_TOOL_DESCRIPTION = (
    "ZOBRAZ zákazníkovi identifikačný formulár (widget) v chat kanáli a požiadaj ho o "
    "identifikačný údaj. Prvotný formulár má len jedno pole; typ údaja rozpozná tool sám. "
    "TOTO JE VÝSTUP PRE ZÁKAZNÍKA A UKONČUJE TVOJ ŤAH: po zavolaní tohto toolu už NEVOLAJ "
    "žiadny ďalší nástroj a počkaj, kým zákazník formulár odošle. Až po jeho odoslaní "
    "(utterance 'identifikacia_widget_submitted') zavolaj identifikacia(). "
    "Mimo chat kanála widget nemá zmysel — vtedy vypýtaj údaj textom."
)

_AUTH_WIDGET_TOOL_DESCRIPTION = (
    "ZOBRAZ zákazníkovi autentifikačný formulár (widget) pre aktuálny overovací faktor v chat "
    "kanáli. Faktor sa určí automaticky z priebehu overenia (voliteľne zadaj faktor: "
    "meno_priezvisko / kod_adresata / rc_last4). Vyžaduje predchádzajúcu identifikáciu. "
    "TOTO JE VÝSTUP PRE ZÁKAZNÍKA A UKONČUJE TVOJ ŤAH: po zavolaní už NEVOLAJ žiadny ďalší "
    "nástroj a počkaj na odpoveď. Až po odoslaní zavolaj autentifikacia()."
)

_AUTH_TOOL_DESCRIPTION = (
    "Overí totožnosť zákazníka. Volaj OPAKOVANE — pri každom volaní zaeviduje "
    "aktuálny faktor a vráti, čo treba ešte. Najprv treba zavolať jeden z "
    "identifikacia_* toolov (autentifikacia vychádza z výsledku identifikácie). "
    "Faktory sa pýtajú v pevnom poradí: 1) dôveryhodný zdroj (automaticky z volania), "
    "2) meno a priezvisko, 3) kód adresáta z faktúry, 4) posledné 4 cifry rodného čísla. "
    "Pre štandardné transakcie treba 2 faktory, pre citlivé 3 faktory. "
    "Bez parametrov tool vyhodnotí aktuálny stav a vráti ďalší krok. "
    "Ak zákazník daný údaj nemá, zavolaj s skip_current_factor=true."
)

_TEST_KONTEXT_DESCRIPTION = (
    "DEBUG/TEST IBA: nastaví hodnoty v lokálnej mirror cache, ktoré inak prichádzajú "
    "z NLP engine cez named_entities (input_source = telefón/email zákazníka z caller ID, "
    "authentication_type = 'standard' alebo 'sensitive' podľa zámeru transakcie). "
    "V produkčnom prostredí tieto hodnoty plne setuje NLP engine."
)

_VIAZANOST_TOOL_DESCRIPTION = (
    "Overí aktívne zmluvy (viazanosti) identifikovaného a autentifikovaného zákazníka "
    "z Product Inventory DPS. Vyžaduje predchádzajúcu identifikáciu (identifikacia_*) "
    "a štandardnú autentifikáciu (autentifikacia). Vráti klasifikáciu viazanosti "
    "(Nema_viazanost / Prolongacne_okno / Viazanost_do_roka / Viazanost_viac_ako_rok), "
    "surové dáta zmlúv a odporúčanú odpoveď pre zákazníka."
)

# Methods whose input is PII (sent to NLP only as last4=XXXX marker).
_PII_METHODS = frozenset({"rodne_cislo"})


def _persist_identification(
    *,
    conversation_id: str,
    method: str,
    value: str,
    candidates: list[dict[str, Any]],
) -> None:
    """Cache identification result + push named_entities update to the NLP engine.

    PII methods (rodne_cislo, op, pas) push only ``last4=XXXX`` to NLP.
    Non-PII methods (ico, kod_zakaznika, telefon, seriove_cislo) push the
    full normalized value. The cache itself always holds the full value
    (process-local, 30 min TTL).
    """
    if not conversation_id:
        _log.warning("identifikacia_%s: no conversation_id — cache and NLP skipped", method)
        return

    _IDENTITY_STATE.set(
        conversation_id,
        {
            "rc_last4": value[-4:],
            "identification_method": method,
            "identification_value": value,
            "candidates": candidates,
        },
    )

    nlp_value = f"last4={value[-4:]}" if method in _PII_METHODS else value

    _nlp_set_state(
        conversation_id,
        {
            "identification_method": method,
            "identification": nlp_value,
        },
    )


def register(
    registry: ToolRegistry,
    *,
    client: DPSGetClient,
    max_candidates: int = 10,
) -> None:
    """Register identity tools onto the FastMCP registry."""

    async def _identify_and_respond(
        identification_id: str,
        identification_type: str,
        not_found_message: str,
        conversation_id: str,
        log_id_tag: str,
        method: str,
    ) -> str:
        """Shared identification flow used by all identification tools.

        ``identification_id`` must already be validated by the caller.
        """
        _log.info(
            "identification called %s=%s max_candidates=%s",
            log_id_tag,
            identification_id[-4:],
            max_candidates,
        )

        try:
            parties_raw = await client.get_parties_by_identification(
                identification_id, identification_type
            )
        except DPSError as exc:
            _log.warning("party lookup failed (%s): %s", identification_type, exc)
            return _json(_dps_error_payload(exc))

        # Filter to Party records; accept initialized, validated, null/missing.
        # Reject only terminal states: deceased, closed.
        seen: set[str] = set()
        parties: list[dict[str, Any]] = []
        for p in parties_raw:
            if p.get("entityType") != "Party":
                continue
            if p.get("status") in ("deceased", "closed"):
                continue  # accept initialized, validated, null/missing
            pid = p.get("id")
            if not isinstance(pid, str) or pid in seen:
                continue
            seen.add(pid)
            parties.append(p)

        if not parties:
            return _json(
                {
                    "found": False,
                    "error": "not_found",
                    "message": not_found_message,
                }
            )

        capped = parties[:max_candidates]

        # Step B fanout, concurrently per Party.
        try:
            customer_lists = await asyncio.gather(
                *(client.get_customers_by_engaged_party(p["id"]) for p in capped)
            )
        except DPSError as exc:
            _log.warning("customer fanout failed (%s): %s", identification_type, exc)
            return _json(_dps_error_payload(exc))

        candidates: list[dict[str, Any]] = []
        for party, customers in zip(capped, customer_lists, strict=True):
            if customers:
                candidates.extend(_candidate(party, c) for c in customers)
            else:
                candidates.append(_candidate(party, None))

        # Count unique party_ids to determine single vs. multi-match.
        unique_party_ids = {c["party_id"] for c in candidates}

        # Cache full candidates for downstream tools and push NLP state update.
        _persist_identification(
            conversation_id=conversation_id,
            method=method,
            value=identification_id,
            candidates=candidates,
        )

        if len(unique_party_ids) == 1:
            # Single match — return just the name.
            name = candidates[0]["name"]
            return _json({"found": True, "name": name})

        # Multiple matches — return deduplicated sorted names.
        names = sorted({c["name"] for c in candidates if c["name"]})
        return _json(
            {
                "found": True,
                "multiple_matches": True,
                "names": names,
                "message": "Pre toto identifikačné číslo som našla viacero záznamov. Bude potrebné si vyžiadať dodatočné údaje.",
            }
        )

    # --- Internal identifier lookups -------------------------------------
    # Each assumes the value is already validated/normalised by the dispatcher.
    # NLP load/flush is handled once by the `identifikacia` dispatcher, not here.

    async def _lookup_rodne_cislo(value: str, conv: str) -> str:
        return await _identify_and_respond(
            identification_id=value,
            identification_type="socialSecurityNumber",
            not_found_message=_RC_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="rc_last4",
            method="rodne_cislo",
        )

    async def _lookup_ico(value: str, conv: str) -> str:
        return await _identify_and_respond(
            identification_id=value,
            identification_type="subjectRegistrationId",
            not_found_message=_ICO_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="ico_last4",
            method="ico",
        )

    async def _lookup_kod_zakaznika(value: str, conv: str) -> str:
        _log.info("identifikacia kod_zakaznika code_suffix=%s conv=%s", value[-1], conv)
        try:
            if value.endswith("0"):
                customer = await client.get_customer_by_id(value)
            else:
                ba = await client.get_billing_account_by_id(value)
                if ba is None:
                    customer = None
                else:
                    cust_id = (ba.get("customer") or {}).get("id")
                    if not isinstance(cust_id, str):
                        _log.warning(
                            "billing account %s has no customer.id — treating as not_found", value
                        )
                        customer = None
                    else:
                        customer = await client.get_customer_by_id(cust_id)
        except DPSError as exc:
            _log.warning("identifikacia kod_zakaznika failed: %s", exc)
            return _json(_dps_error_payload(exc))

        if customer is None:
            return _json({"found": False, "error": "not_found", "message": _KOD_NOT_FOUND_MESSAGE})

        candidate = _candidate_from_customer(customer)
        if not candidate.get("name"):
            # Defensive: the Customer record exists but has no usable name field
            return _json({"found": False, "error": "not_found", "message": _KOD_NOT_FOUND_MESSAGE})

        _persist_identification(
            conversation_id=conv,
            method="kod_zakaznika",
            value=value,
            candidates=[candidate],
        )

        return _json({"found": True, "name": candidate["name"]})

    async def _lookup_telefon(normalized: str, conv: str) -> str:
        _log.info("identifikacia telefon msisdn_last4=%s conv=%s", normalized[-4:], conv)

        try:
            products = await client.get_products_by_public_identifier(normalized)
        except DPSError as exc:
            _log.warning("identifikacia telefon product lookup failed: %s", exc)
            return _json(_dps_error_payload(exc))

        if not products:
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        # Extract unique customer ids from returned products
        customer_ids: list[str] = []
        seen: set[str] = set()
        for p in products:
            cid = (p.get("customer") or {}).get("id")
            if isinstance(cid, str) and cid not in seen:
                seen.add(cid)
                customer_ids.append(cid)

        if not customer_ids:
            _log.warning("identifikacia telefon: products returned but no customer.id linkage")
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        # Fetch each unique customer
        try:
            customers_raw = await asyncio.gather(
                *(client.get_customer_by_id(cid) for cid in customer_ids)
            )
        except DPSError as exc:
            _log.warning("identifikacia telefon customer fanout failed: %s", exc)
            return _json(_dps_error_payload(exc))

        customers = [c for c in customers_raw if c is not None]
        if not customers:
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        candidates = [_candidate_from_customer(c) for c in customers]
        candidates = [c for c in candidates if c.get("name")]
        if not candidates:
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        # The queried MSISDN matched the customer's Product.publicIdentifier — that is a
        # verified mobile contact for this customer. Inject it into candidate.contacts so
        # the auth tool's `trusted_source` factor can match against it without an extra
        # Party fetch.
        for cand in candidates:
            existing = list(cand.get("contacts") or [])
            if not any(
                ct.get("type") == "mobile"
                and _normalize_msisdn(ct.get("value") or "") == normalized
                for ct in existing
            ):
                existing.append({"type": "mobile", "value": normalized})
                cand["contacts"] = existing

        _persist_identification(
            conversation_id=conv,
            method="telefon",
            value=normalized,
            candidates=candidates,
        )

        if len(candidates) == 1:
            return _json({"found": True, "name": candidates[0]["name"]})

        names = sorted({c["name"] for c in candidates if c["name"]})
        return _json(
            {
                "found": True,
                "multiple_matches": True,
                "names": names,
                "message": (
                    "Pre toto telefónne číslo som našla viacero záznamov. "
                    "Bude potrebné si vyžiadať dodatočné údaje."
                ),
            }
        )

    async def _lookup_seriove_cislo(normalized: str, conv: str) -> str:
        _log.info(
            "identifikacia seriove_cislo serial_last4=%s conv=%s", normalized[-4:], conv
        )

        try:
            products = await client.get_products_by_serial_number(normalized)
        except DPSError as exc:
            _log.warning("identifikacia seriove_cislo product lookup failed: %s", exc)
            return _json(_dps_error_payload(exc))

        if not products:
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        # Extract unique customer ids
        customer_ids: list[str] = []
        seen: set[str] = set()
        for p in products:
            cid = (p.get("customer") or {}).get("id")
            if isinstance(cid, str) and cid not in seen:
                seen.add(cid)
                customer_ids.append(cid)

        if not customer_ids:
            _log.warning(
                "identifikacia seriove_cislo: products returned but no customer.id linkage"
            )
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        try:
            customers_raw = await asyncio.gather(
                *(client.get_customer_by_id(cid) for cid in customer_ids)
            )
        except DPSError as exc:
            _log.warning("identifikacia seriove_cislo customer fanout failed: %s", exc)
            return _json(_dps_error_payload(exc))

        customers = [c for c in customers_raw if c is not None]
        if not customers:
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        candidates = [_candidate_from_customer(c) for c in customers]
        candidates = [c for c in candidates if c.get("name")]
        if not candidates:
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        _persist_identification(
            conversation_id=conv,
            method="seriove_cislo",
            value=normalized,
            candidates=candidates,
        )

        if len(candidates) == 1:
            return _json({"found": True, "name": candidates[0]["name"]})

        names = sorted({c["name"] for c in candidates if c["name"]})
        return _json(
            {
                "found": True,
                "multiple_matches": True,
                "names": names,
                "message": (
                    "Pre toto sériové číslo som našla viacero záznamov. "
                    "Bude potrebné si vyžiadať dodatočné údaje."
                ),
            }
        )

    # --- Dispatcher: the single public identification tool ----------------

    async def _route_identifikacia(typ: str, hodnota: str, conv: str) -> str:
        """Validate/normalise per type then call the matching internal lookup."""
        if typ == _TYPE_TELEFON:
            normalized = _normalize_msisdn(hodnota)
            if not normalized:
                return _json({"found": False, "error": "invalid_input", "message": _TEL_INVALID_MESSAGE})
            return await _lookup_telefon(normalized, conv)
        if typ == _TYPE_ICO:
            value = hodnota.strip()
            if not _ICO_PATTERN.fullmatch(value):
                return _json({"found": False, "error": "invalid_input", "message": _ICO_INVALID_MESSAGE})
            return await _lookup_ico(value, conv)
        if typ == _TYPE_RODNE_CISLO:
            value = hodnota.strip()
            if not _RC_PATTERN.fullmatch(value):
                return _json({"found": False, "error": "invalid_input", "message": _RC_INVALID_MESSAGE})
            return await _lookup_rodne_cislo(value, conv)
        if typ == _TYPE_KOD_ZAKAZNIKA:
            value = hodnota.strip()
            if not _KOD_PATTERN.fullmatch(value):
                return _json({"found": False, "error": "invalid_input", "message": _KOD_INVALID_MESSAGE})
            return await _lookup_kod_zakaznika(value, conv)
        if typ == _TYPE_SERIOVE_CISLO:
            normalized = _normalize_serial(hodnota)
            if not normalized:
                return _json({"found": False, "error": "invalid_input", "message": _SERIAL_INVALID_MESSAGE})
            return await _lookup_seriove_cislo(normalized, conv)
        return _json({"found": False, "error": "invalid_input", "message": _IDENT_UNRECOGNIZED_MESSAGE})

    @mcp_tool(name="identifikacia", description=_IDENTIFIKACIA_TOOL_DESCRIPTION, registry=registry)
    async def identifikacia(
        hodnota: Annotated[
            str | None,
            Field(
                description=(
                    "Identifikačný údaj zákazníka (telefón, IČO, rodné číslo, kód zákazníka, "
                    "sériové číslo). Nechaj prázdne v chat kanáli — zobrazí sa widget a hodnotu "
                    "si tool prečíta z pamäte konverzácie."
                )
            ),
        ] = None,
        typ: Annotated[
            str | None,
            Field(
                description=(
                    "Voliteľný typ údaja: telefon | ico | rodne_cislo | kod_zakaznika | "
                    "seriove_cislo | auto. Default 'auto' — typ sa rozpozná automaticky podľa formátu."
                )
            ),
        ] = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        await _nlp_load(conv)
        try:
            nlp = _nlp_get_named_entities(conv)
            channel = (nlp.get(_CHANNEL_KEY) or "").strip().lower()
            hodnota = (hodnota or nlp.get(widgets.IDENT_INPUT_KEY) or "").strip()
            typ = (typ or nlp.get(widgets.IDENT_TYPE_KEY) or "").strip().lower()

            # No value and no explicit type → this tool does NOT render a widget.
            # Tell the LLM to show the form (chat) or ask for the value (non-chat).
            # (An explicit `typ` with an empty value falls through to per-type
            # validation below, which returns invalid_input.)
            if not hodnota and typ in ("", _TYPE_AUTO):
                instruction = (
                    "V chat kanáli zobraz formulár cez zobraz_identifikacny_widget a počkaj na "
                    "odpoveď zákazníka."
                    if channel == _CHANNEL_CHAT
                    else "Zisti od zákazníka identifikačný údaj a zavolaj identifikacia(hodnota=<údaj>)."
                )
                return _json(
                    {
                        "found": False,
                        "error": "input_required",
                        "suggested_response": _IDENT_INPUT_REQUIRED_MESSAGE,
                        "instruction": instruction,
                    }
                )

            # Resolve the type — explicit dropdown/param value wins over auto-detection.
            if typ in ("", _TYPE_AUTO):
                detected, alternatives = _classify_identifier(hodnota)
                if detected is None:
                    if alternatives and channel == _CHANNEL_CHAT:
                        # Genuine collision → ask the customer to pick the type via the widget.
                        return _json(
                            {
                                "found": False,
                                "error": "ambiguous_type",
                                "alternatives": alternatives,
                                "suggested_response": _IDENT_AMBIGUOUS_MESSAGE,
                                "instruction": (
                                    "Zobraz zobraz_identifikacny_widget(s_vyberom_typu=True), nech "
                                    "zákazník vyberie typ údaja, a počkaj na odoslanie."
                                ),
                            }
                        )
                    return _json(
                        {
                            "found": False,
                            "error": "invalid_input",
                            "message": _IDENT_UNRECOGNIZED_MESSAGE,
                        }
                    )
                typ = detected

            return await _route_identifikacia(typ, hodnota, conv)
        finally:
            _nlp_flush(conv)

    @mcp_tool(
        name="zobraz_identifikacny_widget",
        description=_IDENT_WIDGET_TOOL_DESCRIPTION,
        registry=registry,
    )
    async def zobraz_identifikacny_widget(
        s_vyberom_typu: Annotated[  # noqa: FBT002
            bool,
            Field(
                description="True iba keď identifikacia vrátila error=ambiguous_type — pridá do "
                "formulára výber typu údaja. Inak nechaj False (jednoduchý formulár)."
            ),
        ] = False,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        await _nlp_load(conv)
        channel = (_nlp_get_named_entities(conv).get(_CHANNEL_KEY) or "").strip().lower()
        if channel != _CHANNEL_CHAT:
            return _json(
                {
                    "rendered": False,
                    "reason": "not_chat",
                    "suggested_response": _IDENT_INPUT_REQUIRED_MESSAGE,
                    "instruction": (
                        "Toto nie je chat kanál — widget sa nezobrazí. Vypýtaj údaj textom "
                        "a zavolaj identifikacia(hodnota=<údaj>)."
                    ),
                }
            )
        caption = (
            "Zadaný údaj sa dal rozpoznať viacerými spôsobmi — vyberte, prosím, jeho typ."
            if s_vyberom_typu
            else None
        )
        return _json(
            bubble_widget_result(
                summary="Identifikačný widget",
                widget=widgets.identifikacia_widget(caption=caption, with_type_select=s_vyberom_typu),
                template="identifikacia",
                assistant_text="Pošlite mi, prosím, identifikačný údaj cez tento formulár.",
            )
        )

    @mcp_tool(
        name="zobraz_autentifikacny_widget",
        description=_AUTH_WIDGET_TOOL_DESCRIPTION,
        registry=registry,
    )
    async def zobraz_autentifikacny_widget(
        faktor: Annotated[
            str | None,
            Field(
                description="Voliteľný faktor: meno_priezvisko | kod_adresata | rc_last4. "
                "Ak vynecháš, určí sa automaticky podľa priebehu overenia."
            ),
        ] = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        await _nlp_load(conv)
        channel = (_nlp_get_named_entities(conv).get(_CHANNEL_KEY) or "").strip().lower()
        if channel != _CHANNEL_CHAT:
            return _json(
                {
                    "rendered": False,
                    "reason": "not_chat",
                    "instruction": "Mimo chatu zbieraj overovací údaj textom cez autentifikacia(...).",
                }
            )
        # LLM may pass the factor under its widget key (meno_priezvisko) or the
        # internal factor name (name) — accept both.
        requested = (faktor or "").strip()
        factor = {"meno_priezvisko": _FACTOR_NAME}.get(requested, requested)
        if factor not in widgets.AUTH_FIELD_KEYS:
            factor = _peek_next_auth_factor(conv)
        if factor not in widgets.AUTH_FIELD_KEYS:
            return _json(
                {
                    "rendered": False,
                    "reason": "no_factor",
                    "instruction": (
                        "Nemám aktuálny overovací faktor — najprv identifikuj zákazníka a "
                        "vyhodnoť stav cez autentifikacia()."
                    ),
                }
            )
        suggested = _suggested_response_for_factor(factor)
        return _json(
            bubble_widget_result(
                summary=f"Autentifikačný widget ({factor})",
                widget=widgets.auth_factor_widget(factor, caption=suggested),
                template="autentifikacia",
                assistant_text=suggested,
            )
        )

    @mcp_tool(name="autentifikacia", description=_AUTH_TOOL_DESCRIPTION, registry=registry)
    async def autentifikacia(
        meno_priezvisko: Annotated[
            str | None,
            Field(
                description="Meno a priezvisko zákazníka (faktor 2). Neposielaj naraz s inými faktormi."
            ),
        ] = None,
        kod_adresata: Annotated[
            str | None,
            Field(description="Kód adresáta = kód fakturačného účtu z faktúry (faktor 3)."),
        ] = None,
        rc_last4: Annotated[
            str | None,
            Field(description="Posledné 4 cifry rodného čísla (faktor 4)."),
        ] = None,
        skip_current_factor: Annotated[  # noqa: FBT002
            bool,
            Field(
                description="True ak zákazník nemá údaj na aktuálne pýtaný faktor — preskočiť na ďalší."
            ),
        ] = False,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json(
                {
                    "authenticated": False,
                    "error": "missing_conversation_id",
                    "message": "Vyskytol sa technický problém. Prepojím vás na operátora.",
                }
            )

        await _nlp_load(conv)
        try:
            # Step 1: identification must exist
            identity = _IDENTITY_STATE.get(conv)
            if not identity:
                return _json(
                    {
                        "authenticated": False,
                        "error": "identification_required",
                        "suggested_response": (
                            "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                            "kód zákazníka alebo telefónne číslo?"
                        ),
                        "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                    }
                )

            candidates = identity.get("candidates") or []
            if not candidates:
                return _json(
                    {
                        "authenticated": False,
                        "error": "identification_required",
                        "suggested_response": (
                            "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                            "kód zákazníka alebo telefónne číslo?"
                        ),
                        "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                    }
                )

            # If multi-match identification, can't authenticate against ambiguous candidate
            if len(candidates) > 1:
                return _json(
                    {
                        "authenticated": False,
                        "error": "ambiguous_identification",
                        "suggested_response": (
                            "Z identifikácie máme viacero záznamov, potrebujeme jednoznačnú identifikáciu pred overením."
                        ),
                        "instruction": "Zaveď zákazníka cez presnejší identifikačný údaj.",
                    }
                )

            candidate = candidates[0]
            identification_method = identity.get("identification_method") or ""
            identification_value = identity.get("identification_value") or ""

            # Step 2: load or init auth state
            state = _AUTH_STATE.get(conv) or {
                "factors_satisfied": set(),
                "factors_failed": set(),
                "factors_skipped": set(),
                "factors_attempts": {},
                "authenticated_standard": False,
                "authenticated_sensitive": False,
            }
            # Convert lists from cached dict back to sets (TTLStore stores whatever we put in)
            for key in ("factors_satisfied", "factors_failed", "factors_skipped"):
                if isinstance(state.get(key), list):
                    state[key] = set(state[key])

            # Step 3: read NLP mirror
            nlp = _nlp_get_named_entities(conv)
            input_source = nlp.get("input_source", "")
            channel = (nlp.get(_CHANNEL_KEY) or "").strip().lower()
            auth_type = nlp.get("authentication_type") or _AUTH_TYPE_STANDARD
            if auth_type not in (_AUTH_TYPE_STANDARD, _AUTH_TYPE_SENSITIVE):
                auth_type = _AUTH_TYPE_STANDARD

            # Chat widget submit path: a factor value entered in the auth widget lands
            # in named_entities. Pull it (consume-once) when the LLM didn't pass a param.
            if meno_priezvisko is None:
                meno_priezvisko = _consume_named_entity(conv, widgets.AUTH_FIELD_KEYS["name"])
            if kod_adresata is None:
                kod_adresata = _consume_named_entity(conv, widgets.AUTH_FIELD_KEYS["kod_adresata"])
            if rc_last4 is None:
                rc_last4 = _consume_named_entity(conv, widgets.AUTH_FIELD_KEYS["rc_last4"])

            # Step 4: auto-credit factors (recompute every call — input_source may arrive late)
            credited = _credited_factors_from_identification(
                identification_method,
                identification_value,
                candidate.get("contacts") or [],
                input_source,
            )
            state["factors_satisfied"] |= credited

            blocked = state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]

            # Auto-skip trusted_source if input_source is missing (nothing the caller can do).
            # This must happen before out-of-order checks so that factor 2 is the first "expected".
            if (
                _FACTOR_TRUSTED_SOURCE not in state["factors_satisfied"]
                and _FACTOR_TRUSTED_SOURCE not in blocked
                and not input_source
            ):
                state["factors_skipped"].add(_FACTOR_TRUSTED_SOURCE)
                blocked = (
                    state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]
                )

            expected = _next_factor(blocked)

            # Step 5: process user-provided input (one factor per call)
            provided: list[tuple[str, str]] = []
            if meno_priezvisko is not None:
                provided.append((_FACTOR_NAME, meno_priezvisko))
            if kod_adresata is not None:
                provided.append((_FACTOR_KOD_ADRESATA, kod_adresata))
            if rc_last4 is not None:
                provided.append((_FACTOR_RC_LAST4, rc_last4))

            if len(provided) > 1:
                return _json(
                    {
                        "authenticated": False,
                        "error": "multiple_factors_in_call",
                        "suggested_response": "Vyskytol sa technický problém. Prepojím vás na operátora.",
                        "instruction": "Pošli vždy len jeden faktor naraz.",
                    }
                )

            if provided:
                factor, value = provided[0]
                if expected != factor:
                    return _json(
                        {
                            "authenticated": False,
                            "error": "out_of_order",
                            "expected_factor": expected,
                            "suggested_response": _suggested_response_for_factor(expected)
                            if expected
                            else "Pre opätovné vyhodnotenie zavolaj autentifikacia.",
                            "instruction": _instruction_for_factor(expected)
                            if expected
                            else "Zavolaj autentifikacia bez parametrov.",
                        }
                    )
                # Verify
                ok = False
                if factor == _FACTOR_NAME:
                    ok = _check_name(value, candidate.get("name"))
                elif factor == _FACTOR_KOD_ADRESATA:
                    ok = _check_kod_adresata(value, candidate.get("billing_account_ids") or [])
                elif factor == _FACTOR_RC_LAST4:
                    rc4 = candidate.get("auth_rc_last4")
                    if rc4 is None:
                        # Lazy-fetch Party to get RČ last4 (identified via customer-only path)
                        party_id = candidate.get("party_id")
                        if isinstance(party_id, str) and party_id:
                            try:
                                party = await client.get_party_by_id(party_id)
                            except DPSError as exc:
                                _log.warning("autentifikacia: party fetch failed for rc check: %s", exc)
                                return _json(_dps_error_payload(exc))
                            if party is not None:
                                rc4 = _extract_rc_last4_from_party(party)
                                if rc4:
                                    candidate["auth_rc_last4"] = rc4
                                    # Write back the enriched candidate to identity cache
                                    _IDENTITY_STATE.set(conv, identity)
                    ok = _check_rc_last4(value, rc4)

                attempts_map = state["factors_attempts"]
                if ok:
                    state["factors_satisfied"].add(factor)
                    attempts_map.pop(factor, None)
                else:
                    attempts_map[factor] = attempts_map.get(factor, 0) + 1
                    if attempts_map[factor] >= _MAX_AUTH_ATTEMPTS_PER_FACTOR:
                        state["factors_failed"].add(factor)
                        _persist_auth_state(conv, state)
                        _log.warning(
                            "auth factor=%s failed after %d attempts conv=%s",
                            factor,
                            attempts_map[factor],
                            conv,
                        )
                        # Fall through to recompute next step
                    else:
                        remaining = _MAX_AUTH_ATTEMPTS_PER_FACTOR - attempts_map[factor]
                        _persist_auth_state(conv, state)
                        retry_msg = (
                            f"Tento údaj sa nezhoduje. Skúste, prosím, znova. "
                            f"Zostáva vám {'ešte jeden pokus' if remaining == 1 else f'{remaining} pokusy'}."
                        )
                        retry_instruction = (
                            "V chat kanáli zobraz znova zobraz_autentifikacny_widget a počkaj na odoslanie."
                            if channel == _CHANNEL_CHAT and factor in widgets.AUTH_FIELD_KEYS
                            else _instruction_for_factor(factor)
                        )
                        return _json(
                            {
                                "authenticated": False,
                                "factor_failed": factor,
                                "attempts_remaining": remaining,
                                "suggested_response": retry_msg,
                                "instruction": retry_instruction,
                            }
                        )

            # Step 6: skip current factor if requested
            if skip_current_factor and expected and expected != _FACTOR_TRUSTED_SOURCE:
                state["factors_skipped"].add(expected)

            blocked = state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]

            # Step 7: check if we have enough
            required = _required_factors(auth_type)
            satisfied_count = len(state["factors_satisfied"])

            if satisfied_count >= required:
                state["authenticated_standard"] = True
                if satisfied_count >= 3 or auth_type == _AUTH_TYPE_SENSITIVE:
                    state["authenticated_sensitive"] = satisfied_count >= 3
                _persist_auth_state(conv, state)
                level = (
                    _AUTH_TYPE_SENSITIVE if state["authenticated_sensitive"] else _AUTH_TYPE_STANDARD
                )
                _nlp_set_state(
                    conv,
                    {
                        "authenticated_standard": "true",
                        "authenticated_sensitive": "true"
                        if state["authenticated_sensitive"]
                        else "false",
                        "authentication_level": level,
                    },
                )
                return _json(
                    {
                        "authenticated": True,
                        "level": level,
                        "factors_satisfied": sorted(state["factors_satisfied"]),
                        "suggested_response": "Ďakujem, overenie prebehlo úspešne. S čím vám môžem pomôcť?",
                    }
                )

            # Step 8: find next factor
            next_f = _next_factor(blocked)
            _persist_auth_state(conv, state)

            if next_f is None:
                return _json(
                    {
                        "authenticated": False,
                        "error": "cannot_authenticate",
                        "factors_satisfied": sorted(state["factors_satisfied"]),
                        "factors_failed": sorted(state["factors_failed"]),
                        "factors_skipped": sorted(state["factors_skipped"]),
                        "suggested_response": "Overenie sa nepodarilo. Prepájam vás na operátora.",
                        "instruction": "Eskaluj na ľudského operátora.",
                    }
                )

            if next_f == _FACTOR_TRUSTED_SOURCE:
                # Trusted source is automatic — nothing the caller can do.
                # Mark it as skipped (input_source missing or didn't match) and re-eval.
                state["factors_skipped"].add(_FACTOR_TRUSTED_SOURCE)
                _persist_auth_state(conv, state)
                next_f = _next_factor(
                    state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]
                )
                if next_f is None:
                    return _json(
                        {
                            "authenticated": False,
                            "error": "cannot_authenticate",
                            "suggested_response": "Overenie sa nepodarilo. Prepájam vás na operátora.",
                            "instruction": "Eskaluj na ľudského operátora.",
                        }
                    )

            return _need_factor_response(
                next_f,
                auth_type=auth_type,
                satisfied=state["factors_satisfied"],
                required=required,
                channel=channel,
            )
        finally:
            _nlp_flush(conv)

    @mcp_tool(name="nastav_test_kontext", description=_TEST_KONTEXT_DESCRIPTION, registry=registry)
    async def nastav_test_kontext(
        input_source: Annotated[
            str | None,
            Field(
                description="Telefónne číslo alebo email volajúceho — simuluje caller-ID / from-header z NLP."
            ),
        ] = None,
        authentication_type: Annotated[
            str | None,
            Field(description="'standard' alebo 'sensitive' — simuluje typ transakcie z NLP."),
        ] = None,
        channel: Annotated[
            str | None,
            Field(
                description="Kanál konverzácie, napr. 'chat' (zapne widgety) alebo 'voice' — "
                "simuluje named_entity Channel z NLP."
            ),
        ] = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json({"ok": False, "error": "missing_conversation_id"})

        await _nlp_load(conv)
        # Debug tool: write straight to the read mirror (simulate NLP-provided
        # named_entities). These are NOT queued for push back to the NLP engine.
        entities: dict[str, str] = {}
        if input_source is not None:
            entities["input_source"] = input_source
        if authentication_type is not None:
            entities["authentication_type"] = authentication_type
        if channel is not None:
            entities[_CHANNEL_KEY] = channel
        if entities:
            current = _NLP_MIRROR_STATE.get(conv) or {}
            current.update(entities)
            _NLP_MIRROR_STATE.set(conv, current)
        return _json({"ok": True, "named_entities": dict(_NLP_MIRROR_STATE.get(conv) or {})})

    @mcp_tool(name="over_viazanost", description=_VIAZANOST_TOOL_DESCRIPTION, registry=registry)
    async def over_viazanost(_meta: dict[str, Any] | None = None) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json({
                "found": False,
                "error": "missing_conversation_id",
                "message": _UPSTREAM_ERROR_MESSAGE,
            })

        await _nlp_load(conv)
        try:
            # Require standard authentication before returning contract data.
            auth_state = _AUTH_STATE.get(conv)
            if not auth_state or not auth_state.get("authenticated_standard"):
                return _json({
                    "found": False,
                    "error": "authentication_required",
                    "suggested_response": (
                        "Na zobrazenie informácií o viazanosti musím najprv overiť vašu totožnosť."
                    ),
                    "instruction": "Zavolaj autentifikacia tool a až potom over_viazanost.",
                })

            identity = _IDENTITY_STATE.get(conv)
            if not identity:
                return _json({
                    "found": False,
                    "error": "identification_required",
                    "suggested_response": (
                        "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                        "kód zákazníka alebo telefónne číslo?"
                    ),
                    "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                })

            candidates = identity.get("candidates") or []
            if not candidates:
                return _json({
                    "found": False,
                    "error": "identification_required",
                    "suggested_response": (
                        "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                        "kód zákazníka alebo telefónne číslo?"
                    ),
                    "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                })

            candidate = candidates[0]
            customer_id: str | None = candidate.get("customer_id") or None
            identification_method = identity.get("identification_method") or ""
            identification_value = identity.get("identification_value") or ""
            # Prefer customer_id (covers all products); fall back to MSISDN only when
            # identification was by phone and no customer_id linkage exists.
            msisdn: str | None = (
                identification_value if (not customer_id and identification_method == "telefon") else None
            )

            _log.info(
                "over_viazanost: conv=%s customer_id_last4=%s method=%s",
                conv,
                (customer_id or "")[-4:] or "none",
                identification_method,
            )

            try:
                products_raw = await client.get_products_for_agreements(
                    customer_id=customer_id,
                    msisdn=msisdn,
                )
            except DPSError as exc:
                _log.warning("over_viazanost: PI fetch failed: %s", exc)
                return _json(_dps_error_payload(exc))

            today = date.today()
            active_products = _parse_products_with_active_agreements(products_raw, today)
            viazanost_typ, suggested_response, latest_date = _classify_viazanost(active_products, today)

            _log.info(
                "over_viazanost: conv=%s fetched=%d active=%d typ=%s",
                conv,
                len(products_raw),
                len(active_products),
                viazanost_typ,
            )

            # Sort ascending by viazanost_do (soonest commitment ends first)
            active_products.sort(key=lambda p: p.get("viazanost_do") or "")

            # Per-product typ classification
            def _product_typ(viazanost_do_str: str | None) -> str:
                d = _parse_agreement_date(viazanost_do_str or "")
                if d is None:
                    return "Chyba"
                days = (d - today).days
                if days < 90:
                    return "Prolongacne_okno"
                if days < 365:
                    return "Viazanost_do_roka"
                return "Viazanost_viac_ako_rok"

            # Group by typ, preserve _VIAZANOST_TYP_ORDER
            grouped: dict[str, list[dict[str, Any]]] = {}
            for p in active_products:
                typ = _product_typ(p.get("viazanost_do"))
                grouped.setdefault(typ, []).append(p)
            services_grouped = {k: grouped[k] for k in _VIAZANOST_TYP_ORDER if k in grouped}

            _nlp_set_state(conv, {"over_viazanost_result": viazanost_typ})
            return _json({
                "found": True,
                "viazanost_typ": viazanost_typ,
                "viazanost_do": str(latest_date) if latest_date else None,
                "services": services_grouped,
                "count": len(active_products),
                "suggested_response": suggested_response,
            })
        finally:
            _nlp_flush(conv)
