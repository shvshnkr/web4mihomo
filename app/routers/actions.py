"""HTMX partial routes (mutations and delay tests)."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.deps import SettingsDep, require_ui_session_htmx
from app.mihomo_client import MihomoAPIError, MihomoClient
from app.models import AddProxyForm, AddSubscriptionForm, ProxyStore, StoredProxy, StoredSubscription
from app.uri_to_proxy import build_proxy_dict_from_uri, suggest_proxy_name_from_uri
from app.store_json import StoreJson
from app.sync_service import apply_auto_filter_policy, persist_and_reload, unique_proxy_name_from_store
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
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _render_dashboard(
    request: Request,
    settings: SettingsDep,
    store: ProxyStore,
    *,
    message: str | None = None,
    message_kind: str = "info",
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
        },
    )


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
    lines = split_bulk_vless_lines(form.raw)
    log.info("POST /htmx/add: строк в поле=%d", len(lines))

    if not lines:
        return _render_dashboard(
            request,
            settings,
            store,
            message="Вставьте хотя бы одну строку с vless:// или trojan:// (можно несколько, по одной на строку).",
            message_kind="error",
        )

    errors: list[str] = []
    added = 0
    for idx, line in enumerate(lines, start=1):
        log.debug("  строка %d: начало разбора (первые 72 символа): %r", idx, line[:72])
        low = line.lower()
        if not (low.startswith("vless://") or low.startswith("trojan://")):
            errors.append(f"Строка {idx}: не vless:// и не trojan:// — пропуск.")
            continue
        try:
            base_name = suggest_proxy_name_from_uri(line)
            name = unique_proxy_name_from_store(store, base_name)
            build_proxy_dict_from_uri(line, name)
            item = StoredProxy(uri=line, proxy_name=name, proxy_payload=None)
            store = ProxyStore(
                proxies=[*store.proxies, item],
                subscriptions=store.subscriptions,
                ui_auto_filter_enabled=store.ui_auto_filter_enabled,
                ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
            )
            added += 1
            log.info("  строка %d: добавлен узел «%s»", idx, name)
        except ValueError as e:
            errors.append(f"Строка {idx}: {e}")
            log.warning("  строка %d: ошибка: %s", idx, e)

    if added == 0:
        msg = "Не добавлено ни одного узла.\n" + "\n".join(errors[:25])
        if len(errors) > 25:
            msg += f"\n… ещё {len(errors) - 25} ошибок."
        return _render_dashboard(request, settings, store, message=msg, message_kind="error")

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

    existed = next((s for s in store.subscriptions if s.url == form.url), None)
    if existed:
        if form.name:
            existed.name = form.name
        store = ProxyStore(
            proxies=store.proxies,
            subscriptions=store.subscriptions,
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        )
    else:
        store = ProxyStore(
            proxies=store.proxies,
            subscriptions=[
                *store.subscriptions,
                StoredSubscription(url=form.url, name=form.name),
            ],
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        )
    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=True)
    st.save(updated)
    msg = "Подписка добавлена и обновлена." if not existed else "Подписка уже была в списке, данные обновлены."
    if err:
        msg = f"{msg}\n\nmihomo не перезагрузил провайдер: {err}"
    return _render_dashboard(request, settings, updated, message=msg, message_kind="error" if err else "info")


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
):
    st = _store(settings)
    store = st.load()
    is_enabled = str(enabled).lower() in {"1", "true", "on", "yes"}
    max_delay = max(100, min(120000, int(max_delay_ms or 1500)))
    store.ui_auto_filter_enabled = is_enabled
    store.ui_auto_filter_max_delay_ms = max_delay
    _dbg(
        "H11",
        "app/routers/actions.py:htmx_auto_filter_config",
        "auto_filter_config_saved",
        {
            "enabled": is_enabled,
            "max_delay_ms": max_delay,
            "store_value_enabled": store.ui_auto_filter_enabled,
            "store_value_max_delay_ms": store.ui_auto_filter_max_delay_ms,
        },
    )
    if not is_enabled:
        for s in store.subscriptions:
            s.auto_excluded_uris = []
    updated, err = await persist_and_reload(settings, store, refresh_subscriptions=False)
    st.save(updated)
    msg = f"Auto-filter {'включен' if is_enabled else 'выключен'}, порог {max_delay} ms."
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
    _dbg(
        "H4",
        "app/routers/actions.py:htmx_test_all",
        "test_all_start",
        {"proxies_count": len(store.proxies), "concurrency": settings.test_all_concurrency},
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

    await asyncio.gather(*(one(p) for p in store.proxies))
    _dbg(
        "H6",
        "app/routers/actions.py:htmx_test_all",
        "test_all_after_gather",
        {
            "with_delay": sum(1 for p in store.proxies if p.last_delay_ms is not None),
            "with_error": sum(1 for p in store.proxies if p.last_delay_error),
            "total": len(store.proxies),
        },
    )
    store = apply_auto_filter_policy(store, effective_settings)
    _dbg(
        "H10",
        "app/routers/actions.py:htmx_test_all",
        "test_all_after_auto_filter",
        {
            "auto_excluded_total": sum(len(s.auto_excluded_uris) for s in store.subscriptions),
            "manual_excluded_total": sum(len(s.excluded_uris) for s in store.subscriptions),
            "proxies_total": len(store.proxies),
        },
    )
    updated, err = await persist_and_reload(effective_settings, store)
    st.save(updated)
    log.info("POST /htmx/test-all: завершено для %d узлов", len(store.proxies))
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
