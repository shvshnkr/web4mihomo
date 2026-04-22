"""Client for provider subscription endpoints returning JSON with ``links``."""

from __future__ import annotations

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
    """Fetch and validate JSON payload from subscription URL."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SubscriptionFetchError(f"Не удалось загрузить подписку: {e}") from e

    try:
        data = resp.json()
    except ValueError as e:
        raise SubscriptionFetchError("Подписка вернула не-JSON ответ.") from e

    if not isinstance(data, dict):
        raise SubscriptionFetchError("Неверный формат подписки: ожидается JSON-объект.")

    links_raw = data.get("links")
    if not isinstance(links_raw, list):
        raise SubscriptionFetchError("Неверный формат подписки: поле links должно быть массивом.")

    links: list[str] = []
    for v in links_raw:
        if isinstance(v, str) and v.strip():
            links.append(v.strip())
    if not links:
        raise SubscriptionFetchError("Подписка не содержит ссылок в поле links.")

    user = _parse_user(data.get("user"))
    sub_url = data.get("subscriptionUrl")
    if not isinstance(sub_url, str):
        sub_url = None

    return SubscriptionSnapshot(links=links, user=user, subscription_url=sub_url)


def _parse_user(raw: Any) -> SubscriptionUser | None:
    if not isinstance(raw, dict):
        return None
    try:
        return SubscriptionUser.model_validate(raw)
    except Exception:
        return None
