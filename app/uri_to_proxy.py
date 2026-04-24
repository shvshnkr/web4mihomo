"""Unified URI handling for supported schemes."""

from __future__ import annotations

from typing import Any

from app.hysteria2_to_proxy import suggest_hysteria2_proxy_name, to_mihomo_hysteria2_proxy
from app.hysteria2_uri import parse_hysteria2_uri
from app.trojan_to_proxy import suggest_trojan_proxy_name, to_mihomo_trojan_proxy
from app.trojan_uri import parse_trojan_uri
from app.vless_to_proxy import suggest_proxy_name, to_mihomo_proxy
from app.vless_uri import parse_vless_uri

SUPPORTED_SCHEMES = ("vless://", "trojan://", "hysteria2://", "hysteria://")


def scheme_of(uri: str) -> str:
    s = (uri or "").strip().lower()
    for prefix in SUPPORTED_SCHEMES:
        if s.startswith(prefix):
            return prefix[:-3]
    raise ValueError("Поддерживаются ссылки vless://, trojan://, hysteria2:// и hysteria://")


def suggest_proxy_name_from_uri(uri: str) -> str:
    sch = scheme_of(uri)
    if sch == "vless":
        return suggest_proxy_name(parse_vless_uri(uri))
    if sch == "trojan":
        return suggest_trojan_proxy_name(parse_trojan_uri(uri))
    if sch in {"hysteria2", "hysteria"}:
        return suggest_hysteria2_proxy_name(parse_hysteria2_uri(uri))
    raise ValueError(f"Unsupported scheme: {sch}")


def build_proxy_dict_from_uri(uri: str, name: str) -> dict[str, Any]:
    sch = scheme_of(uri)
    if sch == "vless":
        return to_mihomo_proxy(parse_vless_uri(uri), name)
    if sch == "trojan":
        return to_mihomo_trojan_proxy(parse_trojan_uri(uri), name)
    if sch in {"hysteria2", "hysteria"}:
        return to_mihomo_hysteria2_proxy(parse_hysteria2_uri(uri), name)
    raise ValueError(f"Unsupported scheme: {sch}")
