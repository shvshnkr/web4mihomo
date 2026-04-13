"""Convert parsed VLESS URI into a mihomo ``proxies`` item (dict)."""

from __future__ import annotations

import re
from typing import Any

from app.vless_uri import ParsedVless, get_param


def _sanitize_display_name(name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    # Clash names: keep readable subset; collapse whitespace
    name = re.sub(r"\s+", " ", name)
    return name[:64]


def suggest_proxy_name(parsed: ParsedVless) -> str:
    """Derive a default proxy name from fragment or host+port."""
    frag = _sanitize_display_name(parsed.fragment)
    if frag:
        return sanitize_clash_name(frag)
    return sanitize_clash_name(f"{parsed.host}:{parsed.port}")


def sanitize_clash_name(name: str) -> str:
    """Make a safe, readable proxy name for YAML and mihomo."""
    s = (name or "").strip() or "node"
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\-.:\s\u0400-\u04FF]+", "_", s, flags=re.UNICODE)
    s = s.strip()[:56] or "node"
    return s


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.lower() in ("1", "true", "yes", "on")


def normalize_reality_short_id(sid_raw: str | None) -> str | None:
    """
    REALITY short-id: до 8 байт (16 hex-символов), только hex.
    Пустое, ``null``, ``none`` и т.п. → ``None`` (поле в конфиг не включаем).
    """
    if sid_raw is None:
        return None
    s = sid_raw.strip()
    if not s:
        return None
    low = s.lower()
    if low in ("null", "none", "undefined", "false", "~"):
        return None

    cleaned = "".join(ch for ch in s if ch not in " \t-:")
    if not cleaned:
        return None
    if not all(ch in "0123456789abcdefABCDEF" for ch in cleaned):
        raise ValueError(
            "REALITY short-id (параметр sid в ссылке) должен быть hex или пустым. "
            f"Сейчас: {sid_raw!r}. Частая ошибка: sid=null — удалите sid из ссылки или укажите реальный short-id."
        )
    if len(cleaned) % 2 != 0:
        raise ValueError(
            "REALITY short-id (sid): нечётное число hex-цифр; проверьте ссылку."
        )
    nbytes = len(cleaned) // 2
    if nbytes > 8:
        raise ValueError(
            "REALITY short-id (sid): максимум 8 байт (16 hex-символов)."
        )
    return cleaned.lower()


def to_mihomo_proxy(parsed: ParsedVless, name: str) -> dict[str, Any]:
    """Build one proxy dict suitable for mihomo YAML under ``proxies:``."""
    p = parsed.params

    security = (get_param(p, "security") or "none").lower()
    network = (get_param(p, "type", "network") or "tcp").lower()

    proxy: dict[str, Any] = {
        "name": name,
        "type": "vless",
        "server": parsed.host,
        "port": parsed.port,
        "uuid": parsed.uuid,
        "udp": True,
    }

    flow = get_param(p, "flow")
    if flow:
        proxy["flow"] = flow

    enc = get_param(p, "encryption")
    if enc is not None:
        proxy["encryption"] = enc

    packet_encoding = get_param(p, "packetencoding", "packet-encoding")
    if packet_encoding:
        proxy["packet-encoding"] = packet_encoding

    fp = get_param(p, "fp", "client-fingerprint")
    if fp:
        proxy["client-fingerprint"] = fp

    if _truthy(get_param(p, "allowinsecure")):
        proxy["skip-cert-verify"] = True

    # TLS / REALITY
    if security in ("tls", "reality"):
        proxy["tls"] = True

    sni = get_param(p, "sni", "servername")
    if sni:
        proxy["servername"] = sni

    alpn = get_param(p, "alpn")
    if alpn:
        # comma-separated in many share links
        parts = [x.strip() for x in alpn.replace(";", ",").split(",") if x.strip()]
        if parts:
            proxy["alpn"] = parts

    if security == "reality":
        pbk = get_param(p, "pbk", "public-key")
        sid_raw = get_param(p, "sid", "short-id")
        if not pbk:
            raise ValueError("Для REALITY в ссылке нужен параметр pbk (public-key)")
        reality: dict[str, Any] = {"public-key": pbk}
        sid_norm = normalize_reality_short_id(sid_raw)
        if sid_norm is not None:
            reality["short-id"] = sid_norm
        pqv = get_param(p, "pqv")
        if pqv:
            reality["public-key-verify"] = pqv
        proxy["reality-opts"] = reality

    # Transport
    proxy["network"] = network

    if network == "tcp":
        # headerType NONE is default; no extra opts
        pass

    elif network == "ws":
        path = get_param(p, "path", "wspath") or "/"
        headers: dict[str, str] = {}
        host_header = get_param(p, "host", "ws_host", "obfs-host")
        if host_header:
            headers["Host"] = host_header
        # optional custom headers as JSON in some clients — skip if not simple
        ws_opts: dict[str, Any] = {"path": path}
        if headers:
            ws_opts["headers"] = headers
        early_data = get_param(p, "ed", "max-early-data")
        if early_data and early_data.isdigit():
            ws_opts["max-early-data"] = int(early_data)
        proxy["ws-opts"] = ws_opts

    elif network == "grpc":
        service_name = get_param(p, "servicename", "serviceName") or ""
        opts: dict[str, Any] = {}
        if service_name:
            opts["grpc-service-name"] = service_name
        if opts:
            proxy["grpc-opts"] = opts

    elif network == "http":
        path = get_param(p, "path") or "/"
        host_header = get_param(p, "host", "obfs-host")
        http_opts: dict[str, Any] = {"path": path}
        if host_header:
            http_opts["headers"] = {"Host": host_header}
        proxy["http-opts"] = http_opts

    elif network == "h2":
        h2_path = get_param(p, "path") or "/"
        h2_host = get_param(p, "host", "sni")
        h2_opts: dict[str, Any] = {"path": h2_path}
        if h2_host:
            h2_opts["host"] = [h2_host]
        proxy["h2-opts"] = h2_opts

    elif network == "xhttp":
        path = get_param(p, "path") or "/"
        host_header = get_param(p, "host")
        mode = get_param(p, "mode", "xhttp-mode") or "auto"
        xopts: dict[str, Any] = {"path": path, "mode": mode}
        if host_header:
            xopts["host"] = host_header
        extra = get_param(p, "extra")
        if extra:
            xopts["extra"] = extra
        proxy["xhttp-opts"] = xopts

    else:
        raise ValueError(f"Неподдерживаемый transport/network: {network}")

    return proxy
