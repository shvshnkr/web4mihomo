"""HTMX partial routes (mutations and delay tests)."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.deps import SettingsDep, require_ui_session_htmx
from app.mihomo_client import MihomoAPIError, MihomoClient
from app.models import AddProxyForm, AddSubscriptionForm, ProxyStore, StoredProxy, StoredSubscription
from app.subscription_client import SubscriptionFetchError, fetch_subscription_snapshot
from app.uri_to_proxy import build_proxy_dict_from_uri, scheme_of, suggest_proxy_name_from_uri
from app.store_json import StoreJson
from app.sync_service import (
    apply_auto_filter_policy,
    materialize_subscription_proxies,
    persist_and_reload,
    unique_proxy_name,
    unique_proxy_name_from_store,
)
from app.vless_bulk import split_bulk_vless_lines

log = logging.getLogger("web4mihomo.actions")

base = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(base / "templates"))

router = APIRouter(prefix="/htmx", tags=["htmx"])


def _store(settings: SettingsDep) -> StoreJson:
    return StoreJson(settings.json_store_path)


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


def _norm_uri(uri: str) -> str:
    return (uri or "").strip()


def _effective_settings(settings: SettingsDep, store: ProxyStore) -> SettingsDep:
    updates: dict[str, object] = {}
    if store.ui_auto_filter_enabled is not None:
        updates["auto_filter_enabled"] = store.ui_auto_filter_enabled
    if store.ui_auto_filter_max_delay_ms is not None:
        updates["auto_filter_max_delay_ms"] = store.ui_auto_filter_max_delay_ms
    if store.ui_auto_filter_source is not None:
        updates["auto_filter_source"] = store.ui_auto_filter_source
    if store.ui_auto_filter_recheck_interval_sec is not None:
        updates["auto_filter_recheck_interval_sec"] = store.ui_auto_filter_recheck_interval_sec
    if store.ui_auto_filter_recover_streak is not None:
        updates["auto_filter_recover_streak"] = store.ui_auto_filter_recover_streak
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _extract_mihomo_delay_map(payload: dict[str, Any]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    proxies = payload.get("proxies")
    if not isinstance(proxies, dict):
        return out
    for name, node in proxies.items():
        if not isinstance(name, str) or not isinstance(node, dict):
            continue
        delay_value: int | None = None
        history = node.get("history")
        if isinstance(history, list):
            for row in reversed(history):
                if isinstance(row, dict):
                    maybe = row.get("delay")
                    if isinstance(maybe, (int, float)):
                        delay_value = int(maybe)
                        break
        if delay_value is not None:
            out[name] = delay_value if delay_value > 0 else None
            continue
        alive = node.get("alive")
        if alive is False:
            out[name] = None
    return out


def _render_dashboard(
    request: Request,
    settings: SettingsDep,
    store: ProxyStore,
    *,
    message: str | None = None,
    message_kind: str = "info",
    manual_preview: dict[str, Any] | None = None,
    subscription_preview: dict[str, Any] | None = None,
) -> HTMLResponse:
    effective_settings = _effective_settings(settings, store)
    return templates.TemplateResponse(
        request,
        "partials/dashboard.html",
        {
            "request": request,
            "settings": effective_settings,
            "store": store,
            "message": message,
            "message_kind": message_kind,
            "manual_preview": manual_preview,
            "subscription_preview": subscription_preview,
        },
    )


def _store_with_proxy(store: ProxyStore, item: StoredProxy) -> ProxyStore:
    return ProxyStore(
        proxies=[*store.proxies, item],
        subscriptions=store.subscriptions,
        ui_auto_filter_enabled=store.ui_auto_filter_enabled,
        ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        ui_auto_filter_source=store.ui_auto_filter_source,
        ui_auto_filter_recheck_interval_sec=store.ui_auto_filter_recheck_interval_sec,
        ui_auto_filter_recover_streak=store.ui_auto_filter_recover_streak,
    )


def _preview_manual_additions(store: ProxyStore, raw: str) -> dict[str, Any]:
    lines = split_bulk_vless_lines(raw)
    errors: list[str] = []
    valid: list[StoredProxy] = []
    seen_uris: set[str] = set()
    existing_uris = {(p.uri or "").strip() for p in store.proxies if (p.uri or "").strip()}
    draft_store = store

    for idx, line in enumerate(lines, start=1):
        log.debug("  строка %d: preview начало разбора (первые 72 символа): %r", idx, line[:72])
        uri = line.strip()
        if uri in seen_uris:
            errors.append(f"Строка {idx}: дубль в текущем вводе — пропуск.")
            continue
        if uri in existing_uris:
            errors.append(f"Строка {idx}: такой URI уже есть в списке — пропуск.")
            continue
        try:
            scheme_of(uri)
            base_name = suggest_proxy_name_from_uri(uri)
            name = unique_proxy_name_from_store(draft_store, base_name)
            build_proxy_dict_from_uri(uri, name)
            item = StoredProxy(uri=uri, proxy_name=name, proxy_payload=None)
            valid.append(item)
            seen_uris.add(uri)
            draft_store = _store_with_proxy(draft_store, item)
        except ValueError as e:
            errors.append(f"Строка {idx}: {e}")

    return {
        "raw": raw,
        "lines_count": len(lines),
        "valid": valid,
        "errors": errors,
        "draft_store": draft_store,
    }


def _manual_preview_message(preview: dict[str, Any]) -> tuple[str, str]:
    valid_count = len(preview["valid"])
    errors = preview["errors"]
    if valid_count == 0:
        msg = "Preview: не найдено ни одного валидного узла."
        if errors:
            msg += "\n" + "\n".join(errors[:25])
            if len(errors) > 25:
                msg += f"\n… ещё {len(errors) - 25} ошибок."
        return msg, "error"
    msg = f"Preview: готово к добавлению узлов: {valid_count}."
    if errors:
        msg += "\nПредупреждения:\n" + "\n".join(errors[:25])
        if len(errors) > 25:
            msg += f"\n… ещё {len(errors) - 25}."
    return msg, "info"


async def _probe_preview_candidates(
    settings: SettingsDep,
    base_store: ProxyStore,
    candidates: list[StoredProxy],
    *,
    rounds: int = 1,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    if not candidates:
        return {}, None
    client = MihomoClient(settings)
    rounds = max(1, min(5, int(rounds or 1)))
    test_url = settings.delay_test_url
    timeout_ms = settings.delay_timeout_ms
    expected = settings.delay_test_expected
    stats: dict[str, dict[str, Any]] = {
        p.proxy_name: {"alive": 0, "total": rounds, "last_ms": None, "best_ms": None, "error": None}
        for p in candidates
    }

    draft = ProxyStore(
        proxies=[*base_store.proxies, *(p.model_copy(deep=True) for p in candidates)],
        subscriptions=base_store.subscriptions,
        ui_auto_filter_enabled=base_store.ui_auto_filter_enabled,
        ui_auto_filter_max_delay_ms=base_store.ui_auto_filter_max_delay_ms,
        ui_auto_filter_source=base_store.ui_auto_filter_source,
        ui_auto_filter_recheck_interval_sec=base_store.ui_auto_filter_recheck_interval_sec,
        ui_auto_filter_recover_streak=base_store.ui_auto_filter_recover_streak,
    )

    _, prep_err = await persist_and_reload(settings, draft, refresh_subscriptions=False, client=client)
    if prep_err:
        for p in candidates:
            stats[p.proxy_name]["error"] = prep_err
        await persist_and_reload(settings, base_store, refresh_subscriptions=False, client=client)
        return stats, prep_err

    try:
        for _ in range(rounds):
            for p in candidates:
                try:
                    ms = await client.proxy_delay_ms(
                        p.proxy_name,
                        test_url=test_url,
                        timeout_ms=timeout_ms,
                        expected=expected,
                    )
                    st = stats[p.proxy_name]
                    st["alive"] += 1
                    st["last_ms"] = ms
                    prev_best = st["best_ms"]
                    st["best_ms"] = ms if prev_best is None else min(prev_best, ms)
                    st["error"] = None
                except MihomoAPIError as e:
                    stats[p.proxy_name]["last_ms"] = None
                    stats[p.proxy_name]["error"] = str(e)
    finally:
        await persist_and_reload(settings, base_store, refresh_subscriptions=False, client=client)
    return stats, None


@router.post("/add/preview", response_class=HTMLResponse)
async def htmx_add_preview(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
    link: str = Form(""),
):
    st = _store(settings)
    store = st.load()
    form = AddProxyForm.from_form(link)
    preview = _preview_manual_additions(store, form.raw)
    log.info("POST /htmx/add/preview: строк в поле=%d, valid=%d", preview["lines_count"], len(preview["valid"]))
    if not preview["lines_count"]:
        return _render_dashboard(
            request,
            settings,
            store,
            message=(
                "Вставьте хотя бы одну строку с vless://, trojan://, hysteria2://, "
                "hysteria:// или base64 blob со списком URI."
            ),
            message_kind="error",
        )
    ping_stats, ping_err = await _probe_preview_candidates(settings, store, preview["valid"], rounds=1)
    preview["ping_stats"] = ping_stats
    preview["ping_rounds"] = 1
    preview["alive_total"] = sum(1 for st in ping_stats.values() if st.get("alive", 0) > 0)
    preview["checked_total"] = len(ping_stats)
    msg, kind = _manual_preview_message(preview)
    if ping_err:
        msg += f"\nPreview ping: {ping_err}"
        kind = "error"
    elif ping_stats:
        msg += f"\nPreview ping: живых {preview['alive_total']}/{preview['checked_total']}."
    return _render_dashboard(
        request,
        settings,
        store,
        message=msg,
        message_kind=kind,
        manual_preview=preview,
    )


@router.post("/add/confirm", response_class=HTMLResponse)
@router.post("/add", response_class=HTMLResponse)
async def htmx_add(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
    link: str = Form(""),
):
    st = _store(settings)
    store = st.load()
    form = AddProxyForm.from_form(link)
    preview = _preview_manual_additions(store, form.raw)
    added = len(preview["valid"])
    errors = preview["errors"]

    if added == 0:
        msg, kind = _manual_preview_message(preview)
        return _render_dashboard(
            request,
            settings,
            store,
            message=msg,
            message_kind=kind,
            manual_preview=preview if preview["lines_count"] else None,
        )

    store = preview["draft_store"]

    st.save(store)
    log.info("Сохранён JSON, вызываю persist_and_reload (%d новых узлов)", added)
    updated, err = await persist_and_reload(settings, store)
    st.save(updated)

    parts = [f"Добавлено узлов: {added}."]
    if errors:
        parts.append("Предупреждения:\n" + "\n".join(errors[:25]))
        if len(errors) > 25:
            parts.append(f"… ещё {len(errors) - 25}.")
    msg = "\n".join(parts)
    kind: str = "info"
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
        kind = "error"
    return _render_dashboard(request, settings, updated, message=msg, message_kind=kind)


def _preview_subscription_links(store: ProxyStore, links: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    valid: list[dict[str, str]] = []
    seen_uris: set[str] = set()
    existing_names = {p.proxy_name for p in store.proxies if p.source_type != "subscription"}

    for idx, raw_uri in enumerate(links, start=1):
        uri = (raw_uri or "").strip()
        if not uri:
            continue
        if uri in seen_uris:
            errors.append(f"Ссылка {idx}: дубль в подписке — пропуск.")
            continue
        try:
            scheme_of(uri)
            base_name = suggest_proxy_name_from_uri(uri)
            name = unique_proxy_name(base_name, existing_names)
            build_proxy_dict_from_uri(uri, name)
            existing_names.add(name)
            valid.append({"uri": uri, "proxy_name": name})
            seen_uris.add(uri)
        except ValueError as e:
            errors.append(f"Ссылка {idx}: {e}")

    return {
        "links_count": len(links),
        "valid": valid,
        "errors": errors,
    }


def _subscription_preview_message(preview: dict[str, Any]) -> tuple[str, str]:
    valid_count = len(preview["valid"])
    errors = preview["errors"]
    if valid_count == 0:
        msg = "Preview подписки: валидных узлов не найдено."
        if errors:
            msg += "\n" + "\n".join(errors[:25])
            if len(errors) > 25:
                msg += f"\n… ещё {len(errors) - 25} ошибок."
        return msg, "error"
    msg = (
        f"Preview подписки: ссылок получено {preview['links_count']}, "
        f"к добавлению/обновлению валидных узлов {valid_count}."
    )
    if errors:
        msg += "\nПредупреждения:\n" + "\n".join(errors[:25])
        if len(errors) > 25:
            msg += f"\n… ещё {len(errors) - 25}."
    ping_err = preview.get("ping_error")
    if ping_err:
        msg += f"\nPreview ping: {ping_err}"
        return msg, "error"
    checked_total = int(preview.get("checked_total") or 0)
    if checked_total > 0:
        msg += f"\nPreview ping: живых {preview.get('alive_total', 0)}/{checked_total}."
    return msg, "info"


async def _build_subscription_preview(
    store: ProxyStore,
    settings: SettingsDep,
    form: AddSubscriptionForm,
) -> dict[str, Any]:
    snap = await fetch_subscription_snapshot(
        form.url,
        timeout_s=settings.subscriptions_fetch_timeout_sec,
    )
    preview = _preview_subscription_links(store, snap.links)
    preview.update(
        {
            "url": form.url,
            "name": form.name,
            "subscription_url": snap.subscription_url or form.url,
            "user": snap.user,
        }
    )
    candidates = [
        StoredProxy(uri=v["uri"], proxy_name=v["proxy_name"], source_type="subscription")
        for v in preview["valid"]
    ]
    ping_stats, ping_err = await _probe_preview_candidates(settings, store, candidates, rounds=1)
    preview["ping_stats"] = ping_stats
    preview["ping_rounds"] = 1
    preview["alive_total"] = sum(1 for st in ping_stats.values() if st.get("alive", 0) > 0)
    preview["checked_total"] = len(ping_stats)
    preview["ping_error"] = ping_err
    return preview


@router.post("/subscription/preview", response_class=HTMLResponse)
async def htmx_subscription_preview(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
    url: str = Form(""),
    name: str = Form(""),
):
    st = _store(settings)
    store = st.load()
    form = AddSubscriptionForm.from_form(url=url, name=name)
    if not form.url:
        return _render_dashboard(
            request,
            settings,
            store,
            message="Укажите URL подписки.",
            message_kind="error",
        )
    try:
        preview = await _build_subscription_preview(store, settings, form)
    except SubscriptionFetchError as e:
        return _render_dashboard(
            request,
            settings,
            store,
            message=f"Preview подписки не выполнен: {e}",
            message_kind="error",
            subscription_preview={"url": form.url, "name": form.name, "valid": [], "errors": [str(e)], "links_count": 0},
        )
    msg, kind = _subscription_preview_message(preview)
    return _render_dashboard(
        request,
        settings,
        store,
        message=msg,
        message_kind=kind,
        subscription_preview=preview,
    )


@router.post("/subscription/add", response_class=HTMLResponse)
async def htmx_subscription_add(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
    url: str = Form(""),
    name: str = Form(""),
):
    st = _store(settings)
    store = st.load()
    form = AddSubscriptionForm.from_form(url=url, name=name)
    if not form.url:
        return _render_dashboard(
            request,
            settings,
            store,
            message="Укажите URL подписки.",
            message_kind="error",
        )

    try:
        preview = await _build_subscription_preview(store, settings, form)
    except SubscriptionFetchError as e:
        return _render_dashboard(
            request,
            settings,
            store,
            message=f"Подписка не добавлена: {e}",
            message_kind="error",
            subscription_preview={"url": form.url, "name": form.name, "valid": [], "errors": [str(e)], "links_count": 0},
        )

    if not preview["valid"]:
        msg, kind = _subscription_preview_message(preview)
        return _render_dashboard(
            request,
            settings,
            store,
            message=msg,
            message_kind=kind,
            subscription_preview=preview,
        )

    effective_url = preview["subscription_url"]
    existed = next((s for s in store.subscriptions if s.url in {form.url, effective_url}), None)
    now = datetime.now(timezone.utc).isoformat()
    if existed:
        updated_sub = existed.model_copy(deep=True)
        if form.name:
            updated_sub.name = form.name
        updated_sub.url = effective_url
        updated_sub.links = [v["uri"] for v in preview["valid"]]
        updated_sub.user = preview.get("user")
        updated_sub.last_error = None
        updated_sub.last_refresh_at = now
        subscriptions = [updated_sub if s.id == existed.id else s for s in store.subscriptions]
    else:
        subscriptions = [
            *store.subscriptions,
            StoredSubscription(
                url=effective_url,
                name=form.name,
                links=[v["uri"] for v in preview["valid"]],
                user=preview.get("user"),
                last_refresh_at=now,
                last_error=None,
            ),
        ]
    store = ProxyStore(
        proxies=store.proxies,
        subscriptions=subscriptions,
        ui_auto_filter_enabled=store.ui_auto_filter_enabled,
        ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        ui_auto_filter_source=store.ui_auto_filter_source,
        ui_auto_filter_recheck_interval_sec=store.ui_auto_filter_recheck_interval_sec,
        ui_auto_filter_recover_streak=store.ui_auto_filter_recover_streak,
    )
    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=False)
    st.save(updated)
    msg = (
        "Подписка добавлена из preview."
        if not existed
        else "Подписка уже была в списке, данные обновлены из preview."
    )
    if preview["errors"]:
        msg += "\nПредупреждения:\n" + "\n".join(preview["errors"][:25])
        if len(preview["errors"]) > 25:
            msg += f"\n… ещё {len(preview['errors']) - 25}."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(
        request,
        settings,
        updated,
        message=msg,
        message_kind="error" if err else "info",
    )


@router.post("/subscription/{subscription_id}/refresh", response_class=HTMLResponse)
async def htmx_subscription_refresh(
    request: Request,
    subscription_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    st = _store(settings)
    store = st.load()
    if not next((s for s in store.subscriptions if s.id == subscription_id), None):
        return _render_dashboard(request, settings, store, message="Подписка не найдена.", message_kind="error")

    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=True)
    st.save(updated)
    msg = "Подписка обновлена."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


@router.post("/subscription/{subscription_id}/toggle", response_class=HTMLResponse)
async def htmx_subscription_toggle(
    request: Request,
    subscription_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    st = _store(settings)
    store = st.load()
    target = next((s for s in store.subscriptions if s.id == subscription_id), None)
    if not target:
        return _render_dashboard(request, settings, store, message="Подписка не найдена.", message_kind="error")
    target.enabled = not target.enabled
    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=target.enabled)
    st.save(updated)
    state = "включена" if target.enabled else "выключена"
    msg = f"Подписка {state}."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


@router.delete("/subscription/{subscription_id}", response_class=HTMLResponse)
async def htmx_subscription_delete(
    request: Request,
    subscription_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    st = _store(settings)
    store = st.load()
    before = len(store.subscriptions)
    store = ProxyStore(
        proxies=[p for p in store.proxies if p.subscription_id != subscription_id],
        subscriptions=[s for s in store.subscriptions if s.id != subscription_id],
        ui_auto_filter_enabled=store.ui_auto_filter_enabled,
        ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        ui_auto_filter_source=store.ui_auto_filter_source,
        ui_auto_filter_recheck_interval_sec=store.ui_auto_filter_recheck_interval_sec,
        ui_auto_filter_recover_streak=store.ui_auto_filter_recover_streak,
    )
    if len(store.subscriptions) == before:
        return _render_dashboard(request, settings, store, message="Подписка не найдена.", message_kind="error")
    updated, err = await persist_and_reload(settings, store)
    st.save(updated)
    msg = "Подписка удалена."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


@router.post("/subscription/{subscription_id}/exclude/{proxy_id}", response_class=HTMLResponse)
async def htmx_subscription_exclude_proxy(
    request: Request,
    subscription_id: str,
    proxy_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    st = _store(settings)
    store = st.load()
    sub = next((s for s in store.subscriptions if s.id == subscription_id), None)
    if not sub:
        return _render_dashboard(request, settings, store, message="Подписка не найдена.", message_kind="error")

    item = next((p for p in store.proxies if p.id == proxy_id), None)
    if not item or item.source_type != "subscription" or item.subscription_id != subscription_id or not item.uri:
        return _render_dashboard(
            request,
            settings,
            store,
            message="Нельзя исключить этот узел: он не принадлежит выбранной подписке.",
            message_kind="error",
        )

    uri = _norm_uri(item.uri)
    excluded = {_norm_uri(u) for u in sub.excluded_uris if _norm_uri(u)}
    excluded.add(uri)
    sub.excluded_uris = sorted(excluded)
    updated, err = await persist_and_reload(settings, store)
    st.save(updated)
    msg = "Узел исключен из подписки."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


@router.post("/subscription/{subscription_id}/restore", response_class=HTMLResponse)
async def htmx_subscription_restore_uri(
    request: Request,
    subscription_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
    uri: str = Form(""),
):
    st = _store(settings)
    store = st.load()
    sub = next((s for s in store.subscriptions if s.id == subscription_id), None)
    if not sub:
        return _render_dashboard(request, settings, store, message="Подписка не найдена.", message_kind="error")

    target = _norm_uri(uri)
    if not target:
        return _render_dashboard(request, settings, store, message="URI для восстановления пустой.", message_kind="error")

    before = len(sub.excluded_uris)
    sub.excluded_uris = [u for u in sub.excluded_uris if _norm_uri(u) != target]
    if len(sub.excluded_uris) == before:
        return _render_dashboard(request, settings, store, message="Исключение не найдено.", message_kind="error")

    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=True)
    st.save(updated)
    msg = "Исключение снято, узел может вернуться после refresh."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


@router.post("/subscription/{subscription_id}/restore-auto", response_class=HTMLResponse)
async def htmx_subscription_restore_auto(
    request: Request,
    subscription_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    st = _store(settings)
    store = st.load()
    sub = next((s for s in store.subscriptions if s.id == subscription_id), None)
    if not sub:
        return _render_dashboard(request, settings, store, message="Подписка не найдена.", message_kind="error")
    sub.auto_excluded_uris = []
    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=True)
    st.save(updated)
    msg = "Auto-exclude очищен для подписки."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


@router.post("/auto-filter/config", response_class=HTMLResponse)
async def htmx_auto_filter_config(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
    enabled: str = Form(""),
    max_delay_ms: int = Form(1500),
    source: str = Form("hybrid"),
    recheck_interval_sec: int = Form(300),
    recover_streak: int = Form(2),
):
    st = _store(settings)
    store = st.load()
    is_enabled = str(enabled).lower() in {"1", "true", "on", "yes"}
    max_delay = max(100, min(120000, int(max_delay_ms or 1500)))
    src = (source or "hybrid").strip().lower()
    if src not in {"delay", "mihomo", "hybrid"}:
        src = "hybrid"
    recheck_interval = max(30, min(86400, int(recheck_interval_sec or 300)))
    recover_streak_value = max(1, min(20, int(recover_streak or 2)))
    store.ui_auto_filter_enabled = is_enabled
    store.ui_auto_filter_max_delay_ms = max_delay
    store.ui_auto_filter_source = src
    store.ui_auto_filter_recheck_interval_sec = recheck_interval
    store.ui_auto_filter_recover_streak = recover_streak_value
    _dbg(
        "H11",
        "app/routers/actions.py:htmx_auto_filter_config",
        "auto_filter_config_saved",
        {
            "enabled": is_enabled,
            "max_delay_ms": max_delay,
            "source": src,
            "store_value_enabled": store.ui_auto_filter_enabled,
            "store_value_max_delay_ms": store.ui_auto_filter_max_delay_ms,
            "store_value_source": store.ui_auto_filter_source,
            "store_value_recheck_interval_sec": store.ui_auto_filter_recheck_interval_sec,
            "store_value_recover_streak": store.ui_auto_filter_recover_streak,
        },
    )
    if not is_enabled:
        for s in store.subscriptions:
            s.auto_excluded_uris = []
    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=False)
    st.save(updated)
    msg = (
        f"Auto-filter {'включен' if is_enabled else 'выключен'}, порог {max_delay} ms, "
        f"source={src}, recheck={recheck_interval}s, recover_streak={recover_streak_value}."
    )
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


@router.delete("/proxy/{proxy_id}", response_class=HTMLResponse)
async def htmx_delete(
    request: Request,
    proxy_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    log.info("DELETE /htmx/proxy/%s", proxy_id)
    st = _store(settings)
    store = st.load()
    store = ProxyStore(
        proxies=[p for p in store.proxies if p.id != proxy_id],
        subscriptions=store.subscriptions,
        ui_auto_filter_enabled=store.ui_auto_filter_enabled,
        ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        ui_auto_filter_source=store.ui_auto_filter_source,
        ui_auto_filter_recheck_interval_sec=store.ui_auto_filter_recheck_interval_sec,
        ui_auto_filter_recover_streak=store.ui_auto_filter_recover_streak,
    )
    st.save(store)
    updated, err = await persist_and_reload(settings, store)
    st.save(updated)
    msg = f"Удалено. Ошибка mihomo: {err}" if err else "Удалено."
    kind = "error" if err else "info"
    return _render_dashboard(request, settings, updated, message=msg, message_kind=kind)


@router.post("/sync", response_class=HTMLResponse)
async def htmx_sync(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    log.info("POST /htmx/sync: старт")
    st = _store(settings)
    store = st.load()
    n_before = len(store.proxies)
    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=True)
    st.save(updated)
    log.info(
        "POST /htmx/sync: готово, было %d узлов в JSON, стало %d, err=%s",
        n_before,
        len(updated.proxies),
        err,
    )
    if err:
        return _render_dashboard(
            request,
            settings,
            updated,
            message=f"Ошибка синхронизации: {err}",
            message_kind="error",
        )
    msg = "Синхронизация с mihomo выполнена."
    if n_before == 0 and len(updated.proxies) > 0:
        msg = (
            f"Из файла провайдера подтянуто {len(updated.proxies)} узл(ов) в список (JSON был пуст). "
            "Исходных vless:// у импортированных записей нет — при желании удалите строку и добавьте ссылкой заново."
        )
    return _render_dashboard(
        request,
        settings,
        updated,
        message=msg,
        message_kind="info",
    )


@router.post("/delay/{proxy_id}", response_class=HTMLResponse)
async def htmx_delay_one(
    request: Request,
    proxy_id: str,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    st = _store(settings)
    store = st.load()
    item = next((p for p in store.proxies if p.id == proxy_id), None)
    if not item:
        return _render_dashboard(
            request,
            settings,
            store,
            message="Запись не найдена.",
            message_kind="error",
        )
    log.info("POST /htmx/delay/%s имя=%r", proxy_id, item.proxy_name)
    client = MihomoClient(settings)
    _dbg(
        "H3",
        "app/routers/actions.py:htmx_delay_one",
        "delay_one_start",
        {"proxy_id": proxy_id, "proxy_name": item.proxy_name},
    )
    try:
        ms = await client.proxy_delay_ms(
            item.proxy_name,
            test_url=settings.delay_test_url,
            timeout_ms=settings.delay_timeout_ms,
            expected=settings.delay_test_expected,
        )
        _patch_delay_ms(store, proxy_id, ms)
        _patch_delay_error(store, proxy_id, None)
        st.save(store)
        return _render_dashboard(
            request,
            settings,
            store,
            message=f"Задержка «{item.proxy_name}»: {ms} ms",
            message_kind="info",
        )
    except MihomoAPIError as e:
        _dbg(
            "H3",
            "app/routers/actions.py:htmx_delay_one",
            "delay_one_mihomo_error",
            {"proxy_id": proxy_id, "error": str(e)},
        )
        _patch_delay_ms(store, proxy_id, None)
        _patch_delay_error(store, proxy_id, str(e))
        st.save(store)
        return _render_dashboard(
            request,
            settings,
            store,
            message=f"Delay test: {e}",
            message_kind="error",
        )
    except Exception as e:
        _dbg(
            "H3",
            "app/routers/actions.py:htmx_delay_one",
            "delay_one_unhandled_exception",
            {"proxy_id": proxy_id, "exc_type": type(e).__name__, "exc": str(e)},
        )
        raise


def _patch_delay_ms(store: ProxyStore, proxy_id: str, ms: int | None) -> None:
    for p in store.proxies:
        if p.id == proxy_id:
            p.last_delay_ms = ms


def _patch_delay_error(store: ProxyStore, proxy_id: str, err: str | None) -> None:
    for p in store.proxies:
        if p.id == proxy_id:
            p.last_delay_error = err


@router.post("/test-all", response_class=HTMLResponse)
async def htmx_test_all(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
):
    log.info("POST /htmx/test-all: старт")
    st = _store(settings)
    store = st.load()
    if not store.proxies:
        return _render_dashboard(
            request,
            settings,
            store,
            message="Нет прокси для проверки.",
            message_kind="info",
        )

    client = MihomoClient(settings)
    sem = asyncio.Semaphore(settings.test_all_concurrency)
    effective_settings = _effective_settings(settings, store)
    test_url = (
        effective_settings.auto_filter_probe_url
        if effective_settings.auto_filter_enabled
        else effective_settings.delay_test_url
    )
    store_for_test = store
    if effective_settings.auto_filter_enabled:
        full_store = materialize_subscription_proxies(store, apply_excludes=False)
        existing_sub_uris = {
            (p.subscription_id, (p.uri or "").strip())
            for p in store.proxies
            if p.source_type == "subscription" and p.subscription_id and (p.uri or "").strip()
        }
        recheck = [
            p.model_copy(deep=True)
            for p in full_store.proxies
            if p.source_type == "subscription"
            and p.subscription_id
            and (p.uri or "").strip()
            and (p.subscription_id, (p.uri or "").strip()) not in existing_sub_uris
        ]
        store_for_test = ProxyStore(
            proxies=[*store.proxies, *recheck],
            subscriptions=[s.model_copy(deep=True) for s in store.subscriptions],
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
            ui_auto_filter_source=store.ui_auto_filter_source,
            ui_auto_filter_recheck_interval_sec=store.ui_auto_filter_recheck_interval_sec,
            ui_auto_filter_recover_streak=store.ui_auto_filter_recover_streak,
        )
        _dbg(
            "H12",
            "app/routers/actions.py:htmx_test_all",
            "test_all_recheck_scope",
            {
                "base_proxies": len(store.proxies),
                "recheck_added": len(recheck),
                "total_tested": len(store_for_test.proxies),
            },
        )
    _dbg(
        "H4",
        "app/routers/actions.py:htmx_test_all",
        "test_all_start",
        {"proxies_count": len(store_for_test.proxies), "concurrency": settings.test_all_concurrency},
    )

    async def one(p: StoredProxy) -> None:
        async with sem:
            try:
                ms = await client.proxy_delay_ms(
                    p.proxy_name,
                    test_url=test_url,
                    timeout_ms=effective_settings.delay_timeout_ms,
                    expected=effective_settings.delay_test_expected,
                )
                p.last_delay_ms = ms
                p.last_delay_error = None
            except MihomoAPIError:
                p.last_delay_ms = None
                p.last_delay_error = "Delay failed"
            except Exception as e:
                _dbg(
                    "H4",
                    "app/routers/actions.py:htmx_test_all",
                    "test_all_unhandled_exception",
                    {"proxy_name": p.proxy_name, "exc_type": type(e).__name__, "exc": str(e)},
                )
                p.last_delay_ms = None
                p.last_delay_error = f"Delay error: {type(e).__name__}"

    await asyncio.gather(*(one(p) for p in store_for_test.proxies))
    _dbg(
        "H6",
        "app/routers/actions.py:htmx_test_all",
        "test_all_after_gather",
        {
            "with_delay": sum(1 for p in store_for_test.proxies if p.last_delay_ms is not None),
            "with_error": sum(1 for p in store_for_test.proxies if p.last_delay_error),
            "total": len(store_for_test.proxies),
        },
    )
    mihomo_delay_map: dict[str, int | None] | None = None
    if effective_settings.auto_filter_enabled and effective_settings.auto_filter_source in {"mihomo", "hybrid"}:
        try:
            payload = await client.get_proxies_payload()
            mihomo_delay_map = _extract_mihomo_delay_map(payload)
            _dbg(
                "H13",
                "app/routers/actions.py:htmx_test_all",
                "test_all_mihomo_health_loaded",
                {"signals": len(mihomo_delay_map)},
            )
        except Exception as e:
            _dbg(
                "H13",
                "app/routers/actions.py:htmx_test_all",
                "test_all_mihomo_health_error",
                {"exc_type": type(e).__name__, "exc": str(e)},
            )
            log.warning("test-all: failed to load /proxies health map: %s", e)
    store_for_test = apply_auto_filter_policy(
        store_for_test,
        effective_settings,
        mihomo_delay_map=mihomo_delay_map,
    )
    _dbg(
        "H10",
        "app/routers/actions.py:htmx_test_all",
        "test_all_after_auto_filter",
        {
            "auto_excluded_total": sum(len(s.auto_excluded_uris) for s in store_for_test.subscriptions),
            "manual_excluded_total": sum(len(s.excluded_uris) for s in store_for_test.subscriptions),
            "proxies_total": len(store_for_test.proxies),
        },
    )
    updated, err = await persist_and_reload(effective_settings, store_for_test)
    st.save(updated)
    log.info("POST /htmx/test-all: завершено для %d узлов", len(store_for_test.proxies))
    auto_excluded = sum(len(s.auto_excluded_uris) for s in updated.subscriptions)
    msg = "Проверка задержек завершена."
    if effective_settings.auto_filter_enabled:
        msg += f" Auto-excluded: {auto_excluded}."
    if err:
        msg += f"\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(
        request,
        settings,
        updated,
        message=msg,
        message_kind="error" if err else "info",
    )


@router.post("/test-all/repeat", response_class=HTMLResponse)
async def htmx_test_all_repeat(
    request: Request,
    settings: SettingsDep,
    _: None = Depends(require_ui_session_htmx),
    rounds: int = Form(3),
):
    st = _store(settings)
    store = st.load()
    if not store.proxies:
        return _render_dashboard(
            request,
            settings,
            store,
            message="Нет прокси для проверки.",
            message_kind="info",
        )

    rounds = max(1, min(10, int(rounds or 3)))
    client = MihomoClient(settings)
    sem = asyncio.Semaphore(settings.test_all_concurrency)
    effective_settings = _effective_settings(settings, store)
    test_url = (
        effective_settings.auto_filter_probe_url
        if effective_settings.auto_filter_enabled
        else effective_settings.delay_test_url
    )

    store_for_test = store
    if effective_settings.auto_filter_enabled:
        full_store = materialize_subscription_proxies(store, apply_excludes=False)
        existing_sub_uris = {
            (p.subscription_id, (p.uri or "").strip())
            for p in store.proxies
            if p.source_type == "subscription" and p.subscription_id and (p.uri or "").strip()
        }
        recheck = [
            p.model_copy(deep=True)
            for p in full_store.proxies
            if p.source_type == "subscription"
            and p.subscription_id
            and (p.uri or "").strip()
            and (p.subscription_id, (p.uri or "").strip()) not in existing_sub_uris
        ]
        store_for_test = ProxyStore(
            proxies=[*store.proxies, *recheck],
            subscriptions=[s.model_copy(deep=True) for s in store.subscriptions],
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
            ui_auto_filter_source=store.ui_auto_filter_source,
            ui_auto_filter_recheck_interval_sec=store.ui_auto_filter_recheck_interval_sec,
            ui_auto_filter_recover_streak=store.ui_auto_filter_recover_streak,
        )

    agg: dict[str, dict[str, Any]] = {
        p.id: {"name": p.proxy_name, "alive": 0, "best_ms": None}
        for p in store_for_test.proxies
    }

    async def one(p: StoredProxy) -> None:
        async with sem:
            try:
                ms = await client.proxy_delay_ms(
                    p.proxy_name,
                    test_url=test_url,
                    timeout_ms=effective_settings.delay_timeout_ms,
                    expected=effective_settings.delay_test_expected,
                )
                p.last_delay_ms = ms
                p.last_delay_error = None
                a = agg[p.id]
                a["alive"] += 1
                a["best_ms"] = ms if a["best_ms"] is None else min(a["best_ms"], ms)
            except MihomoAPIError:
                p.last_delay_ms = None
                p.last_delay_error = "Delay failed"

    for _ in range(rounds):
        await asyncio.gather(*(one(p) for p in store_for_test.proxies))

    mihomo_delay_map: dict[str, int | None] | None = None
    if effective_settings.auto_filter_enabled and effective_settings.auto_filter_source in {"mihomo", "hybrid"}:
        try:
            payload = await client.get_proxies_payload()
            mihomo_delay_map = _extract_mihomo_delay_map(payload)
        except Exception:
            mihomo_delay_map = None

    store_for_test = apply_auto_filter_policy(
        store_for_test,
        effective_settings,
        mihomo_delay_map=mihomo_delay_map,
    )
    updated, err = await persist_and_reload(effective_settings, store_for_test)
    st.save(updated)

    alive_nodes = sum(1 for a in agg.values() if a["alive"] > 0)
    total_nodes = len(agg)
    msg = f"Повторный ping завершен: живых {alive_nodes}/{total_nodes}, прогонов: {rounds}."
    if err:
        msg += f"\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(
        request,
        settings,
        updated,
        message=msg,
        message_kind="error" if err else "info",
    )
