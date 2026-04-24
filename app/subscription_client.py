"""Client for provider subscription endpoints (JSON or URI list text)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.models import SubscriptionUser


class SubscriptionFetchError(ValueError):
    """Raised when subscription payload is unreachable or invalid."""


@dataclass
class SubscriptionSnapshot:
    links: list[str]
    user: SubscriptionUser | None
    subscription_url: str | None


async def fetch_subscription_snapshot(
    url: str,
    *,
    timeout_s: float = 20.0,
) -> SubscriptionSnapshot:
    """Fetch and parse subscription response as JSON or plain URI list."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SubscriptionFetchError(f"Не удалось загрузить подписку: {e}") from e

    text = resp.text or ""
    data = _try_parse_json(text)
    if isinstance(data, dict):
        links = _links_from_json_dict(data)
        if links:
            user = _parse_user(data.get("user"))
            sub_url = data.get("subscriptionUrl")
            if not isinstance(sub_url, str):
                sub_url = None
            return SubscriptionSnapshot(links=links, user=user, subscription_url=sub_url)

    links = links_from_text(text)
    if links:
        return SubscriptionSnapshot(links=links, user=None, subscription_url=url)

    decoded_links = links_from_base64_text(text)
    if decoded_links:
        return SubscriptionSnapshot(links=decoded_links, user=None, subscription_url=url)

    raise SubscriptionFetchError(
        "Подписка не распознана: ожидается JSON с links, текстовый список URI или base64-представление."
    )


def _parse_user(raw: Any) -> SubscriptionUser | None:
    if not isinstance(raw, dict):
        return None
    try:
        return SubscriptionUser.model_validate(raw)
    except Exception:
        return None


def _try_parse_json(text: str) -> Any:
    s = text.strip()
    if not s:
        return None
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def _links_from_json_dict(data: dict[str, Any]) -> list[str]:
    links_raw = data.get("links")
    if not isinstance(links_raw, list):
        return []
    return [v.strip() for v in links_raw if isinstance(v, str) and v.strip()]


def links_from_text(text: str) -> list[str]:
    """Extract URI-looking lines from plain text."""
    links: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            links.append(line)
    return links


def links_from_base64_text(text: str) -> list[str]:
    """Decode a base64 subscription blob and extract URI-looking lines."""
    compact = "".join(text.split())
    if not compact:
        return []
    try:
        padded = compact + ("=" * ((4 - len(compact) % 4) % 4))
        decoded = base64.b64decode(padded, validate=False).decode("utf-8", errors="ignore")
    except Exception:
        return []
    return links_from_text(decoded)
