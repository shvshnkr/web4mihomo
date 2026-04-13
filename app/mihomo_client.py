"""Async HTTP client for mihomo External Controller."""

from __future__ import annotations

from typing import Any

import httpx

from app.settings import Settings


class MihomoAPIError(RuntimeError):
    """Raised when mihomo returns an error or the request fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MihomoClient:
    """Thin wrapper around common external-controller routes."""

    def __init__(self, settings: Settings) -> None:
        self._base = settings.mihomo_base_url.rstrip("/")
        self._secret = settings.mihomo_secret
        self._timeout = httpx.Timeout(30.0, connect=5.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers(),
            timeout=self._timeout,
        )

    async def provider_update(self, provider_name: str) -> None:
        from urllib.parse import quote

        enc = quote(provider_name, safe="")
        async with self._client() as c:
            r = await c.put(f"/providers/proxies/{enc}")
        if r.status_code >= 400:
            raise MihomoAPIError(
                f"PUT provider failed: {r.status_code} {r.text}",
                status_code=r.status_code,
            )

    async def get_proxies_payload(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get("/proxies")
        if r.status_code >= 400:
            raise MihomoAPIError(
                f"GET /proxies failed: {r.status_code} {r.text}",
                status_code=r.status_code,
            )
        return r.json()

    async def proxy_delay_ms(
        self,
        proxy_name: str,
        *,
        test_url: str,
        timeout_ms: int,
        expected: str | None = None,
    ) -> int:
        from urllib.parse import quote

        enc = quote(proxy_name, safe="")
        params: dict[str, str] = {"url": test_url, "timeout": str(timeout_ms)}
        if expected and expected.strip():
            params["expected"] = expected.strip()

        async with self._client() as c:
            r = await c.get(f"/proxies/{enc}/delay", params=params)
        if r.status_code == 200:
            data = r.json()
            delay = int(data.get("delay", 0))
            if delay <= 0:
                raise MihomoAPIError("delay test returned non-positive delay")
            return delay
        body = r.text
        try:
            data = r.json()
            if isinstance(data, dict) and data.get("message"):
                body = str(data["message"])
        except Exception:
            pass
        hint = ""
        if r.status_code == 503:
            hint = (
                " | Часто узел не достучался до тестового URL или обрыв TLS/Reality. "
                "Попробуйте: DELAY_TEST_URL=http://cp.cloudflare.com/generate_204 "
                "или https://1.1.1.1 , увеличьте DELAY_TIMEOUT_MS, смотрите логи mihomo "
                "и проверьте тот же outbound в другом клиенте."
            )
        raise MihomoAPIError(
            f"delay test failed: {r.status_code} {body}{hint}",
            status_code=r.status_code,
        )
