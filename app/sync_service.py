"""Write provider YAML and trigger mihomo file-provider reload."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.mihomo_client import MihomoAPIError, MihomoClient
from app.models import ProxyStore, StoredProxy, StoredSubscription
from app.provider_render import render_provider_yaml
from app.settings import Settings
from app.subscription_client import SubscriptionFetchError, fetch_subscription_snapshot
from app.vless_uri import parse_vless_uri
from app.vless_to_proxy import suggest_proxy_name, to_mihomo_proxy

log = logging.getLogger("web4mihomo.sync")


def unique_proxy_name_from_store(store: ProxyStore, base: str) -> str:
    existing = {p.proxy_name for p in store.proxies}
    return unique_proxy_name(base, existing)


def unique_proxy_name(base: str, existing: set[str]) -> str:
    name = base
    if name not in existing:
        return name
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def hydrate_store_from_provider_yaml(store: ProxyStore, settings: Settings) -> ProxyStore:
    """
    If JSON store is empty but the provider YAML on disk already lists ``proxies``,
    import those entries so the web UI matches mihomo (no original vless:// kept).
    """
    if store.proxies:
        return store
    path = settings.provider_yaml_path
    if not path.is_file():
        log.debug("hydrate: файла провайдера нет: %s", path)
        return store
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            log.debug("hydrate: файл провайдера пуст: %s", path)
            return store
        doc = yaml.safe_load(raw)
    except Exception as e:
        log.warning("hydrate: не удалось прочитать YAML провайдера %s: %s", path, e)
        return store
    if not isinstance(doc, dict):
        return store
    plist = doc.get("proxies") or []
    if not isinstance(plist, list) or not plist:
        return store

    imported: list[StoredProxy] = []
    for i, p in enumerate(plist):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or f"imported-{i}")
        imported.append(
            StoredProxy(
                uri="",
                proxy_name=name,
                proxy_payload=copy.deepcopy(p),
            )
        )
    if not imported:
        return store
    log.info("hydrate: импортировано %d узл(ов) из %s в пустой JSON", len(imported), path)
    return ProxyStore(proxies=imported)


def build_proxy_dicts(store: ProxyStore) -> list[dict[str, Any]]:
    """Build mihomo proxy dicts from stored URIs or imported YAML payloads."""
    out: list[dict[str, Any]] = []
    for item in store.proxies:
        if item.proxy_payload is not None:
            d = copy.deepcopy(item.proxy_payload)
            d["name"] = item.proxy_name
            out.append(d)
        elif item.uri:
            parsed = parse_vless_uri(item.uri)
            out.append(to_mihomo_proxy(parsed, item.proxy_name))
    return out


async def refresh_enabled_subscriptions(store: ProxyStore, settings: Settings) -> ProxyStore:
    """Fetch all enabled subscriptions and store latest links/user snapshot."""
    updated = store.model_copy(deep=True)
    for sub in updated.subscriptions:
        if not sub.enabled:
            continue
        try:
            snap = await fetch_subscription_snapshot(
                sub.url,
                timeout_s=settings.subscriptions_fetch_timeout_sec,
            )
            sub.links = snap.links
            sub.user = snap.user
            if snap.subscription_url:
                sub.url = snap.subscription_url
            sub.last_error = None
            sub.last_refresh_at = datetime.now(timezone.utc).isoformat()
        except SubscriptionFetchError as e:
            sub.last_error = str(e)
            sub.last_refresh_at = datetime.now(timezone.utc).isoformat()
    return updated


def _build_subscription_proxies(sub: StoredSubscription, existing_names: set[str]) -> tuple[list[StoredProxy], str | None]:
    """Create derived StoredProxy rows from subscription links."""
    built: list[StoredProxy] = []
    seen_uris: set[str] = set()
    parse_errors = 0
    for uri in sub.links:
        u = uri.strip()
        if not u or u in seen_uris or not u.lower().startswith("vless://"):
            continue
        seen_uris.add(u)
        try:
            parsed = parse_vless_uri(u)
            base_name = suggest_proxy_name(parsed)
            name = unique_proxy_name(base_name, existing_names)
            existing_names.add(name)
            built.append(
                StoredProxy(
                    uri=u,
                    proxy_name=name,
                    proxy_payload=None,
                    source_type="subscription",
                    subscription_id=sub.id,
                )
            )
        except ValueError:
            parse_errors += 1
    if parse_errors:
        return built, f"Некоторые ссылки не распознаны: {parse_errors}"
    return built, None


def materialize_subscription_proxies(store: ProxyStore) -> ProxyStore:
    """
    Keep manual proxies intact and rebuild subscription-derived proxies
    from current subscription links.
    """
    manual = [p for p in store.proxies if p.source_type != "subscription"]
    existing_names = {p.proxy_name for p in manual}
    generated: list[StoredProxy] = []
    updated_subs = [s.model_copy(deep=True) for s in store.subscriptions]
    for sub in updated_subs:
        if not sub.enabled:
            continue
        built, parse_warn = _build_subscription_proxies(sub, existing_names)
        generated.extend(built)
        if parse_warn and not sub.last_error:
            sub.last_error = parse_warn
    return ProxyStore(proxies=[*manual, *generated], subscriptions=updated_subs)


def write_provider_file(path: Path, proxies: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_provider_yaml(proxies)
    path.write_text(text, encoding="utf-8")


async def persist_and_reload(
    settings: Settings,
    store: ProxyStore,
    *,
    refresh_subscriptions: bool = False,
    client: MihomoClient | None = None,
) -> tuple[ProxyStore, str | None]:
    """
    Optionally pull rows from existing provider YAML if JSON is empty, then
    write YAML and ``PUT`` provider reload.
    """
    if client is None:
        client = MihomoClient(settings)

    if refresh_subscriptions:
        store = await refresh_enabled_subscriptions(store, settings)

    store = materialize_subscription_proxies(store)
    store = hydrate_store_from_provider_yaml(store, settings)
    proxies = build_proxy_dicts(store)
    log.debug(
        "persist: записываю %d узл(ов) в %s, PUT provider=%r",
        len(proxies),
        settings.provider_yaml_path,
        settings.provider_name,
    )
    write_provider_file(settings.provider_yaml_path, proxies)

    sync_error: str | None = None
    updated = store.model_copy(deep=True)
    try:
        await client.provider_update(settings.provider_name)
        log.debug("persist: PUT провайдера успешен")
        for p in updated.proxies:
            p.last_sync_error = None
    except MihomoAPIError as e:
        msg = str(e)
        if not proxies and "doesn't have any proxy" in msg.lower():
            sync_error = None
        else:
            sync_error = msg
            for p in updated.proxies:
                p.last_sync_error = sync_error
    except httpx.HTTPError as e:
        base = settings.mihomo_base_url.rstrip("/")
        sync_error = (
            f"Нет связи с mihomo ({base}): {e}. "
            "Проверьте: сервис mihomo запущен; в конфиге указан external-controller с тем же хостом/портом; "
            "в shell задан MIHOMO_BASE_URL (например http://127.0.0.1:9090), если порт не стандартный. "
            f'Проверка: curl -sS -H "Authorization: Bearer <secret>" {base}/version'
        )
        for p in updated.proxies:
            p.last_sync_error = sync_error

    return updated, sync_error
