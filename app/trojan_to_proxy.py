"""Convert parsed Trojan URI into a mihomo ``proxies`` item (dict)."""

from __future__ import annotations

from typing import Any

from app.vless_to_proxy import sanitize_clash_name
from app.vless_uri import get_param
from app.trojan_uri import ParsedTrojan


def suggest_trojan_proxy_name(parsed: ParsedTrojan) -> str:
    frag = (parsed.fragment or "").strip()
    if frag:
        return sanitize_clash_name(frag)
    return sanitize_clash_name(f"{parsed.host}:{parsed.port}")


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.lower() in ("1", "true", "yes", "on")


def to_mihomo_trojan_proxy(parsed: ParsedTrojan, name: str) -> dict[str, Any]:
    p = parsed.params
    network = (get_param(p, "type", "network") or "tcp").lower()

    proxy: dict[str, Any] = {
        "name": name,
        "type": "trojan",
        "server": parsed.host,
        "port": parsed.port,
        "password": parsed.password,
        "udp": True,
        "tls": True,
    }

    sni = get_param(p, "sni", "servername", "peer")
    if sni:
        proxy["sni"] = sni

    if _truthy(get_param(p, "allowinsecure")):
        proxy["skip-cert-verify"] = True

    alpn = get_param(p, "alpn")
    if alpn:
        parts = [x.strip() for x in alpn.replace(";", ",").split(",") if x.strip()]
        if parts:
            proxy["alpn"] = parts

    fp = get_param(p, "fp", "client-fingerprint")
    if fp:
        proxy["client-fingerprint"] = fp

    proxy["network"] = network
    if network == "tcp":
        return proxy
    if network == "ws":
        path = get_param(p, "path", "wspath") or "/"
        ws_opts: dict[str, Any] = {"path": path}
        host_header = get_param(p, "host", "obfs-host")
        if host_header:
            ws_opts["headers"] = {"Host": host_header}
        proxy["ws-opts"] = ws_opts
        return proxy
    if network == "grpc":
        service_name = get_param(p, "servicename", "serviceName") or ""
        if service_name:
            proxy["grpc-opts"] = {"grpc-service-name": service_name}
        return proxy
    raise ValueError(f"Неподдерживаемый Trojan transport/network: {network}")
