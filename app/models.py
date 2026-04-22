"""Pydantic models for persisted proxies, subscriptions and API payloads."""

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
    source_type: str = Field(
        default="manual",
        description="manual | subscription",
    )
    subscription_id: str | None = Field(
        default=None,
        description="Parent subscription id for subscription-derived proxies.",
    )


class SubscriptionUser(BaseModel):
    """Subset of provider metadata for UI visibility."""

    shortUuid: str | None = None
    username: str | None = None
    daysLeft: int | None = None
    trafficUsed: str | None = None
    trafficLimit: str | None = None
    expiresAt: str | None = None
    isActive: bool | None = None
    userStatus: str | None = None


class StoredSubscription(BaseModel):
    """Saved subscription endpoint and latest fetched snapshot."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(default="")
    url: str = Field(..., description="Subscription endpoint URL.")
    enabled: bool = Field(default=True)
    links: list[str] = Field(default_factory=list)
    user: SubscriptionUser | None = None
    last_refresh_at: str | None = None
    last_error: str | None = None


class ProxyStore(BaseModel):
    """Root JSON document."""

    proxies: list[StoredProxy] = Field(default_factory=list)
    subscriptions: list[StoredSubscription] = Field(default_factory=list)


class AddProxyForm(BaseModel):
    """Сырое поле: одна или несколько строк vless://."""

    raw: str = Field(default="")

    @classmethod
    def from_form(cls, link: str) -> "AddProxyForm":
        return cls(raw=(link or "").strip())


class AddSubscriptionForm(BaseModel):
    """Subscription URL form payload."""

    url: str = Field(default="")
    name: str = Field(default="")

    @classmethod
    def from_form(cls, url: str, name: str) -> "AddSubscriptionForm":
        return cls(url=(url or "").strip(), name=(name or "").strip())


class LoginForm(BaseModel):
    password: str = ""

    @classmethod
    def from_form(cls, password: str) -> "LoginForm":
        return cls(password=password or "")
