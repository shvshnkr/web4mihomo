"""Convert parsed Hysteria2 URI into mihomo ``proxies`` item."""

from __future__ import annotations

from typing import Any

from app.hysteria2_uri import ParsedHysteria2
from app.vless_to_proxy import sanitize_clash_name
from app.vless_uri import get_param


def suggest_hysteria2_proxy_name(parsed: ParsedHysteria2) -> str:
    frag = (parsed.fragment or "").strip()
    if frag:
        return sanitize_clash_name(frag)
    return sanitize_clash_name(f"{parsed.host}:{parsed.port}")


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.lower() in ("1", "true", "yes", "on")


def to_mihomo_hysteria2_proxy(parsed: ParsedHysteria2, name: str) -> dict[str, Any]:
    p = parsed.params
    proxy: dict[str, Any] = {
        "name": name,
        "type": "hysteria2",
        "server": parsed.host,
        "port": parsed.port,
        "password": parsed.password,
        "udp": True,
    }

    sni = get_param(p, "sni", "servername")
    if sni:
        proxy["sni"] = sni

    if _truthy(get_param(p, "insecure", "allowinsecure")):
        proxy["skip-cert-verify"] = True

    obfs = get_param(p, "obfs")
    if obfs:
        proxy["obfs"] = obfs
        obfs_password = get_param(p, "obfs-password", "obfspassword")
        if obfs_password:
            proxy["obfs-password"] = obfs_password

    alpn = get_param(p, "alpn")
    if alpn:
        parts = [x.strip() for x in alpn.replace(";", ",").split(",") if x.strip()]
        if parts:
            proxy["alpn"] = parts

    return proxy
