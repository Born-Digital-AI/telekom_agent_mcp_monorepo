"""Mock customer database for Slovak Telekom selfcare demos.
Identical copy in each project (Dockerfile copies only one project dir).
"""

from __future__ import annotations

from typing import Any

Customer = dict[str, Any]
Service = dict[str, Any]

CUSTOMERS: list[Customer] = [
    # C001: Happy path — all features work
    {
        "id": "C001",
        "name": "Jana Nováková",
        "phone": "+421901111111",
        "kod_adresata": "KA10001",
        "rodne_cislo": "8552127845",
        "email": "jana.novakova@email.sk",
        "ebill_enabled": True,
        "services": [
            {
                "type": "fixed_internet",
                "address": "Hlavná 12, 811 01 Bratislava",
                "router_model": "Sagemcom 5670 AX",
                "service_point_id": "SP001",
            },
            {"type": "mobile", "msisdn": "+421901111111"},
        ],
    },
    # C002: No email → resend_invoice fails
    {
        "id": "C002",
        "name": "Peter Sloboda",
        "phone": "+421902222222",
        "kod_adresata": "KA10002",
        "rodne_cislo": "7610051234",
        "email": None,
        "ebill_enabled": False,
        "services": [
            {
                "type": "fixed_internet",
                "address": "Obchodná 5, 010 01 Žilina",
                "router_model": "Vantiva FGA2235",
                "service_point_id": "SP002",
            },
        ],
    },
    # C003: Email but eBill disabled → resend_invoice fails
    {
        "id": "C003",
        "name": "Mária Horváthová",
        "phone": "+421903333333",
        "kod_adresata": "KA10003",
        "rodne_cislo": "9005158888",
        "email": "m.horvathova@gmail.com",
        "ebill_enabled": False,
        "services": [
            {
                "type": "fixed_internet",
                "address": "SNP 3, 974 01 Banská Bystrica",
                "router_model": "ZTE ENTRY II",
                "service_point_id": "SP003",
            },
        ],
    },
    # C004: Mobile only → THD find_service_point fails
    {
        "id": "C004",
        "name": "Tomáš Kováč",
        "phone": "+421904444444",
        "kod_adresata": "KA10004",
        "rodne_cislo": "8501059999",
        "email": "tkovac@post.sk",
        "ebill_enabled": True,
        "services": [
            {"type": "mobile", "msisdn": "+421904444444"},
        ],
    },
    # C005: Fixed internet but unknown router → get_info_router fails
    {
        "id": "C005",
        "name": "Eva Blahová",
        "phone": "+421905555555",
        "kod_adresata": "KA10005",
        "rodne_cislo": "9256043210",
        "email": "eva.blahova@centrum.sk",
        "ebill_enabled": True,
        "services": [
            {
                "type": "fixed_internet",
                "address": "Mierová 8, 040 01 Košice",
                "router_model": None,
                "service_point_id": "SP005",
            },
        ],
    },
]

# Lookup indexes
_BY_PHONE: dict[str, Customer] = {}
_BY_KOD: dict[str, Customer] = {}
_BY_ID: dict[str, Customer] = {}

for _c in CUSTOMERS:
    _BY_ID[_c["id"]] = _c
    if _c.get("phone"):
        _BY_PHONE[_c["phone"]] = _c
    if _c.get("kod_adresata"):
        _BY_KOD[_c["kod_adresata"].upper()] = _c


def _normalize_phone(phone: str) -> str:
    phone = phone.strip().replace(" ", "")
    if phone.startswith("0") and len(phone) == 10:
        return "+421" + phone[1:]
    if phone.startswith("421") and not phone.startswith("+"):
        return "+" + phone
    return phone


def find_by_phone(phone: str) -> Customer | None:
    return _BY_PHONE.get(_normalize_phone(phone))


def find_by_kod_adresata(kod: str) -> Customer | None:
    return _BY_KOD.get(kod.strip().upper())


def find_by_id(customer_id: str) -> Customer | None:
    return _BY_ID.get(customer_id)


def get_fixed_internet_service(customer: Customer) -> Service | None:
    for svc in customer.get("services", []):
        if svc.get("type") == "fixed_internet":
            return svc
    return None


KNOWN_ROUTER_MODELS = {
    "Sagemcom 5670 AX",
    "Sagemcom 5655 AC",
    "Vantiva FGA2235",
    "ZTE ENTRY II",
    "ADB VV3212",
    "Sercomm Speedport+",
    "Sercomm Speedport Plus 2",
    "Huawei HG8245 H",
}
