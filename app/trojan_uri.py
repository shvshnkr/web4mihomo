"""Parse single-node ``trojan://`` URIs."""

from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True)
class ParsedTrojan:
    password: str
    host: str
    port: int
    fragment: str
    params: dict[str, str]


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def parse_trojan_uri(raw: str) -> ParsedTrojan:
    s = (raw or "").strip()
    if not s.lower().startswith("trojan://"):
        raise ValueError("Ссылка должна начинаться с trojan://")

    parsed = urlparse(s)
    if parsed.scheme.lower() != "trojan":
        raise ValueError("Неверная схема URI")

    password = unquote(parsed.username or "")
    if not password:
        raise ValueError("В ссылке отсутствует пароль Trojan")

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
    return ParsedTrojan(password=password, host=host, port=port, fragment=fragment, params=params)
