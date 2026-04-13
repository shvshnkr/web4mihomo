"""Write provider YAML and trigger mihomo file-provider reload."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.mihomo_client import MihomoAPIError, MihomoClient
from app.models import ProxyStore, StoredProxy
from app.provider_render import render_provider_yaml
from app.settings import Settings
from app.vless_uri import parse_vless_uri
from app.vless_to_proxy import to_mihomo_proxy

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


def write_provider_file(path: Path, proxies: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_provider_yaml(proxies)
    path.write_text(text, encoding="utf-8")


async def persist_and_reload(
    settings: Settings,
    store: ProxyStore,
    *,
    client: MihomoClient | None = None,
) -> tuple[ProxyStore, str | None]:
    """
    Optionally pull rows from existing provider YAML if JSON is empty, then
    write YAML and ``PUT`` provider reload.
    """
    if client is None:
        client = MihomoClient(settings)

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
