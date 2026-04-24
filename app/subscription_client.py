"""Client for provider subscription endpoints (JSON or URI list text)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
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


def _dbg(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "41d724",
            "runId": "sub403-investigation",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        with open("debug-41d724.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # endregion


async def fetch_subscription_snapshot(
    url: str,
    *,
    timeout_s: float = 20.0,
) -> SubscriptionSnapshot:
    """Fetch and parse subscription response as JSON or plain URI list."""
    try:
        _dbg(
            "H1",
            "app/subscription_client.py:fetch_subscription_snapshot",
            "subscription_fetch_start",
            {"url": url, "timeout_s": timeout_s},
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/json,text/plain,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        _dbg(
            "H5",
            "app/subscription_client.py:fetch_subscription_snapshot",
            "subscription_fetch_headers_profile",
            {"ua_prefix": headers["User-Agent"][:32], "accept": headers["Accept"]},
        )
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            _dbg(
                "H2",
                "app/subscription_client.py:fetch_subscription_snapshot",
                "subscription_fetch_response",
                {
                    "request_url": url,
                    "final_url": str(resp.url),
                    "status_code": resp.status_code,
                    "server": resp.headers.get("server"),
                    "content_type": resp.headers.get("content-type"),
                    "content_length": len(resp.text or ""),
                    "via": resp.headers.get("via"),
                    "cf_ray": resp.headers.get("cf-ray"),
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as e:
        status_code = None
        response_url = None
        response_content_type = None
        body_prefix = None
        if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
            status_code = e.response.status_code
            response_url = str(e.response.url)
            response_content_type = e.response.headers.get("content-type")
            body_prefix = (e.response.text or "")[:240]
        _dbg(
            "H3",
            "app/subscription_client.py:fetch_subscription_snapshot",
            "subscription_fetch_http_error",
            {
                "request_url": url,
                "exc_type": type(e).__name__,
                "status_code": status_code,
                "response_url": response_url,
                "response_content_type": response_content_type,
                "body_prefix": body_prefix,
            },
        )
        raise SubscriptionFetchError(f"Не удалось загрузить подписку: {e}") from e

    text = resp.text or ""
    text_links = links_from_text(text)
    b64_links = links_from_base64_text(text)
    json_like = _try_parse_json(text)
    _dbg(
        "H4",
        "app/subscription_client.py:fetch_subscription_snapshot",
        "subscription_payload_probe",
        {
            "request_url": url,
            "json_detected": isinstance(json_like, dict),
            "text_links_count": len(text_links),
            "base64_links_count": len(b64_links),
        },
    )
    data = json_like
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
