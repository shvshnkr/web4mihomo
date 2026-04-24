"""Write provider YAML and trigger mihomo file-provider reload."""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.mihomo_client import MihomoAPIError, MihomoClient
from app.models import ProxyStore, StoredProxy, StoredSubscription, SubscriptionNodeStats
from app.provider_render import render_provider_yaml
from app.settings import Settings
from app.subscription_client import SubscriptionFetchError, fetch_subscription_snapshot
from app.uri_to_proxy import build_proxy_dict_from_uri, suggest_proxy_name_from_uri

log = logging.getLogger("web4mihomo.sync")


def _dbg(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "41d724",
            "runId": "pre-fix-root-cause",
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
    return ProxyStore(proxies=imported, subscriptions=store.subscriptions)


def build_proxy_dicts(store: ProxyStore) -> list[dict[str, Any]]:
    """Build mihomo proxy dicts from stored URIs or imported YAML payloads."""
    out: list[dict[str, Any]] = []
    for item in store.proxies:
        if item.proxy_payload is not None:
            d = copy.deepcopy(item.proxy_payload)
            d["name"] = item.proxy_name
            out.append(d)
        elif item.uri:
            out.append(build_proxy_dict_from_uri(item.uri, item.proxy_name))
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


def _build_subscription_proxies(
    sub: StoredSubscription,
    existing_names: set[str],
    *,
    apply_excludes: bool,
) -> tuple[list[StoredProxy], str | None]:
    """Create derived StoredProxy rows from subscription links."""
    built: list[StoredProxy] = []
    seen_uris: set[str] = set()
    excluded: set[str] = set()
    if apply_excludes:
        excluded = {
            u.strip()
            for u in [*sub.excluded_uris, *sub.auto_excluded_uris]
            if u and u.strip()
        }
    parse_errors = 0
    for uri in sub.links:
        u = uri.strip()
        if not u or u in excluded or u in seen_uris:
            continue
        low = u.lower()
        if not (low.startswith("vless://") or low.startswith("trojan://")):
            continue
        seen_uris.add(u)
        try:
            base_name = suggest_proxy_name_from_uri(u)
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


def materialize_subscription_proxies(store: ProxyStore, *, apply_excludes: bool) -> ProxyStore:
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
        built, parse_warn = _build_subscription_proxies(
            sub,
            existing_names,
            apply_excludes=apply_excludes,
        )
        generated.extend(built)
        if parse_warn and not sub.last_error:
            sub.last_error = parse_warn
    out = ProxyStore(proxies=[*manual, *generated], subscriptions=updated_subs)
    _dbg(
        "H3",
        "app/sync_service.py:materialize_subscription_proxies",
        "materialized",
        {
            "apply_excludes": apply_excludes,
            "manual_count": len(manual),
            "generated_count": len(generated),
            "subscriptions": len(updated_subs),
            "result_count": len(out.proxies),
        },
    )
    return out


def apply_auto_filter_policy(store: ProxyStore, settings: Settings) -> ProxyStore:
    """Update auto-excluded URIs using latest Delay-all results."""
    if not settings.auto_filter_enabled:
        return store

    now = datetime.now(timezone.utc).isoformat()
    updated_subs = [s.model_copy(deep=True) for s in store.subscriptions]
    subs_by_id = {s.id: s for s in updated_subs}

    for p in store.proxies:
        if p.source_type != "subscription" or not p.subscription_id or not p.uri:
            continue
        sub = subs_by_id.get(p.subscription_id)
        if sub is None:
            continue
        uri = p.uri.strip()
        if not uri:
            continue
        stat = sub.node_stats.get(uri, SubscriptionNodeStats())
        stat.last_checked_at = now
        stat.last_delay_ms = p.last_delay_ms

        if p.last_delay_ms is None:
            stat.last_status = "failed"
            stat.fail_streak += 1
        elif p.last_delay_ms > settings.auto_filter_max_delay_ms:
            stat.last_status = "high-delay"
            stat.fail_streak = max(1, stat.fail_streak + 1)
        else:
            stat.last_status = "healthy"
            stat.fail_streak = 0

        sub.node_stats[uri] = stat

    for sub in updated_subs:
        manual_excluded = {u.strip() for u in sub.excluded_uris if u and u.strip()}
        auto_excluded = {u.strip() for u in sub.auto_excluded_uris if u and u.strip()}
        for uri, stat in sub.node_stats.items():
            if uri in manual_excluded:
                continue
            should_exclude = stat.last_status == "high-delay" or stat.fail_streak >= settings.auto_filter_fail_streak
            if should_exclude:
                auto_excluded.add(uri)
            elif stat.last_status == "healthy":
                auto_excluded.discard(uri)
        sub.auto_excluded_uris = sorted(auto_excluded)

    return ProxyStore(proxies=store.proxies, subscriptions=updated_subs)


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

    _dbg(
        "H4",
        "app/sync_service.py:persist_and_reload",
        "persist_start",
        {
            "refresh_subscriptions": refresh_subscriptions,
            "store_proxies_in": len(store.proxies),
            "store_subscriptions_in": len(store.subscriptions),
            "provider_name": settings.provider_name,
            "provider_lb_name": settings.provider_lb_name,
        },
    )
    _dbg(
        "H6",
        "app/sync_service.py:persist_and_reload",
        "input_delay_state",
        {
            "proxies_with_delay": sum(1 for p in store.proxies if p.last_delay_ms is not None),
            "proxies_with_error": sum(1 for p in store.proxies if p.last_sync_error),
            "total_proxies": len(store.proxies),
        },
    )
    store = hydrate_store_from_provider_yaml(store, settings)
    store_full = materialize_subscription_proxies(store, apply_excludes=False)
    store_lb = materialize_subscription_proxies(store, apply_excludes=True)
    manual_excluded = {
        u.strip()
        for s in store.subscriptions
        for u in s.excluded_uris
        if u and u.strip()
    }
    full_excluded_present = sum(
        1
        for p in store_full.proxies
        if p.source_type == "subscription" and p.uri and p.uri.strip() in manual_excluded
    )
    lb_excluded_present = sum(
        1
        for p in store_lb.proxies
        if p.source_type == "subscription" and p.uri and p.uri.strip() in manual_excluded
    )
    _dbg(
        "H8",
        "app/sync_service.py:persist_and_reload",
        "manual_excluded_presence",
        {
            "manual_excluded_total": len(manual_excluded),
            "full_excluded_present": full_excluded_present,
            "lb_excluded_present": lb_excluded_present,
        },
    )

    proxies_full = build_proxy_dicts(store_full)
    proxies_lb = build_proxy_dicts(store_lb)
    log.debug(
        "persist: full=%d -> %s; lb=%d -> %s",
        len(proxies_full),
        settings.provider_yaml_path,
        len(proxies_lb),
        settings.provider_lb_yaml_path,
    )
    write_provider_file(settings.provider_yaml_path, proxies_full)
    write_provider_file(settings.provider_lb_yaml_path, proxies_lb)
    _dbg(
        "H5",
        "app/sync_service.py:persist_and_reload",
        "providers_written",
        {
            "full_count": len(proxies_full),
            "lb_count": len(proxies_lb),
            "full_path": str(settings.provider_yaml_path),
            "lb_path": str(settings.provider_lb_yaml_path),
        },
    )

    sync_error: str | None = None
    updated = store_lb.model_copy(deep=True)
    errors: list[str] = []

    try:
        await client.provider_update(settings.provider_name)
    except MihomoAPIError as e:
        msg = str(e)
        if not proxies_full and "doesn't have any proxy" not in msg.lower():
            errors.append(f"{settings.provider_name}: {msg}")
    except httpx.HTTPError as e:
        errors.append(f"{settings.provider_name}: HTTP error: {e}")

    if settings.provider_lb_name != settings.provider_name:
        try:
            await client.provider_update(settings.provider_lb_name)
        except MihomoAPIError as e:
            msg = str(e)
            if not proxies_lb and "doesn't have any proxy" not in msg.lower():
                errors.append(f"{settings.provider_lb_name}: {msg}")
        except httpx.HTTPError as e:
            errors.append(f"{settings.provider_lb_name}: HTTP error: {e}")

    if errors:
        sync_error = " | ".join(errors)
        for p in updated.proxies:
            p.last_sync_error = sync_error
    else:
        for p in updated.proxies:
            p.last_sync_error = None
    _dbg(
        "H7",
        "app/sync_service.py:persist_and_reload",
        "output_delay_state",
        {
            "updated_proxies_with_delay": sum(1 for p in updated.proxies if p.last_delay_ms is not None),
            "updated_proxies_with_error": sum(1 for p in updated.proxies if p.last_sync_error),
            "updated_total_proxies": len(updated.proxies),
            "sync_error": sync_error,
        },
    )
    _dbg(
        "H5",
        "app/sync_service.py:persist_and_reload",
        "persist_done",
        {
            "updated_proxies": len(updated.proxies),
            "updated_subscriptions": len(updated.subscriptions),
            "sync_error": sync_error,
        },
    )

    return updated, sync_error
