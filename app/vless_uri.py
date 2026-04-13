"""Parse single-node ``vless://`` URIs (not subscription lists)."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True)
class ParsedVless:
    uuid: str
    host: str
    port: int
    fragment: str
    params: dict[str, str]


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def parse_vless_uri(raw: str) -> ParsedVless:
    s = (raw or "").strip()
    if not s.lower().startswith("vless://"):
        raise ValueError("Ссылка должна начинаться с vless://")

    parsed = urlparse(s)
    if parsed.scheme.lower() != "vless":
        raise ValueError("Неверная схема URI")

    uuid = unquote(parsed.username or "")
    if not uuid:
        raise ValueError("В ссылке отсутствует UUID")

    host = parsed.hostname
    if not host:
        raise ValueError("В ссылке отсутствует host")

    if parsed.port is None:
        raise ValueError("В ссылке отсутствует port")
    port = int(parsed.port)

    # parse_qs keeps lists; normalize to first value, case-insensitive keys
    qs = parse_qs(parsed.query, keep_blank_values=True)
    params: dict[str, str] = {}
    for k, vals in qs.items():
        key = (k or "").lower()
        v = _first([unquote(x) for x in vals])
        if v is not None:
            params[key] = v

    fragment = unquote(parsed.fragment or "").strip()
    return ParsedVless(uuid=uuid, host=host, port=port, fragment=fragment, params=params)


def get_param(params: dict[str, str], *keys: str, default: str | None = None) -> str | None:
    """Case-insensitive lookup for the first matching key."""
    lower = {k.lower(): v for k, v in params.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] != "":
            return lower[k.lower()]
    return default


def params_as_any(parsed: ParsedVless) -> dict[str, Any]:
    """Expose params dict for mappers (read-only view)."""
    return dict(parsed.params)
