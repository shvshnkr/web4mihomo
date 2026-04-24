"""Parse single-node ``hysteria2://`` / ``hysteria://`` URIs."""

from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True)
class ParsedHysteria2:
    password: str
    host: str
    port: int
    fragment: str
    params: dict[str, str]
    scheme: str


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def parse_hysteria2_uri(raw: str) -> ParsedHysteria2:
    s = (raw or "").strip()
    low = s.lower()
    if not (low.startswith("hysteria2://") or low.startswith("hysteria://")):
        raise ValueError("Ссылка должна начинаться с hysteria2:// или hysteria://")

    parsed = urlparse(s)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"hysteria2", "hysteria"}:
        raise ValueError("Неверная схема URI")

    password = unquote(parsed.username or "")
    if not password:
        raise ValueError("В ссылке отсутствует пароль Hysteria")

    host = parsed.hostname
    if not host:
        raise ValueError("В ссылке отсутствует host")

    if parsed.port is None:
        raise ValueError("В ссылке отсутствует port")
    port = int(parsed.port)

    qs = parse_qs(parsed.query, keep_blank_values=True)
    params: dict[str, str] = {}
    for k, vals in qs.items():
        key = (k or "").lower()
        v = _first([unquote(x) for x in vals])
        if v is not None:
            params[key] = v

    fragment = unquote(parsed.fragment or "").strip()
    return ParsedHysteria2(
        password=password,
        host=host,
        port=port,
        fragment=fragment,
        params=params,
        scheme=scheme,
    )
