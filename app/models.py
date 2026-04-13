"""Pydantic models for persisted proxies and API payloads."""

import uuid
from typing import Any

from pydantic import BaseModel, Field


class StoredProxy(BaseModel):
    """VLESS link and/or inline mihomo proxy dict (e.g. imported from provider YAML)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    uri: str = Field(
        default="",
        description="Original vless:// URI; пусто если запись импортирована из YAML.",
    )
    proxy_name: str = Field(..., description="Stable name used in mihomo.")
    proxy_payload: dict[str, Any] | None = Field(
        default=None,
        description="Сырой dict узла из YAML провайдера (если нет vless://).",
    )
    last_delay_ms: int | None = None
    last_sync_error: str | None = None


class ProxyStore(BaseModel):
    """Root JSON document."""

    proxies: list[StoredProxy] = Field(default_factory=list)


class AddProxyForm(BaseModel):
    """Сырое поле: одна или несколько строк vless://."""

    raw: str = Field(default="")

    @classmethod
    def from_form(cls, link: str) -> "AddProxyForm":
        return cls(raw=(link or "").strip())


class LoginForm(BaseModel):
    password: str = ""

    @classmethod
    def from_form(cls, password: str) -> "LoginForm":
        return cls(password=password or "")
