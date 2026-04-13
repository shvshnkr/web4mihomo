"""HTMX partial routes (mutations and delay tests)."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.deps import SettingsDep, require_ui_session_htmx
from app.mihomo_client import MihomoAPIError, MihomoClient
from app.models import AddProxyForm, ProxyStore, StoredProxy
from app.store_json import StoreJson
from app.sync_service import persist_and_reload, unique_proxy_name_from_store
from app.vless_bulk import split_bulk_vless_lines
from app.vless_uri import parse_vless_uri
from app.vless_to_proxy import suggest_proxy_name, to_mihomo_proxy

log = logging.getLogger("web4mihomo.actions")

base = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(base / "templates"))

router = APIRouter(prefix="/htmx", tags=["htmx"])


def _store(settings: SettingsDep) -> StoreJson:
    return StoreJson(settings.json_store_path)


def _render_dashboard(
    request: Request,
    settings: SettingsDep,
    store: ProxyStore,
    *,
    message: str | None = None,
    message_kind: str = "info",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/dashboard.html",
        {
            "request": request,
            "settings": settings,
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
            message="Вставьте хотя бы одну строку с vless:// (можно несколько, по одной на строку).",
            message_kind="error",
        )

    errors: list[str] = []
    added = 0
    for idx, line in enumerate(lines, start=1):
        log.debug("  строка %d: начало разбора (первые 72 символа): %r", idx, line[:72])
        if not line.lower().startswith("vless://"):
            errors.append(f"Строка {idx}: не vless:// — пропуск.")
            continue
        try:
            parsed = parse_vless_uri(line)
            base_name = suggest_proxy_name(parsed)
            name = unique_proxy_name_from_store(store, base_name)
            to_mihomo_proxy(parsed, name)
            item = StoredProxy(uri=line, proxy_name=name, proxy_payload=None)
            store = ProxyStore(proxies=[*store.proxies, item])
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
    store = ProxyStore(proxies=[p for p in store.proxies if p.id != proxy_id])
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
    updated, err = await persist_and_reload(settings, store)
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
    try:
        ms = await client.proxy_delay_ms(
            item.proxy_name,
            test_url=settings.delay_test_url,
            timeout_ms=settings.delay_timeout_ms,
            expected=settings.delay_test_expected,
        )
        _patch_delay_ms(store, proxy_id, ms)
        st.save(store)
        return _render_dashboard(
            request,
            settings,
            store,
            message=f"Задержка «{item.proxy_name}»: {ms} ms",
            message_kind="info",
        )
    except MihomoAPIError as e:
        _patch_delay_ms(store, proxy_id, None)
        st.save(store)
        return _render_dashboard(
            request,
            settings,
            store,
            message=f"Delay test: {e}",
            message_kind="error",
        )


def _patch_delay_ms(store: ProxyStore, proxy_id: str, ms: int | None) -> None:
    for p in store.proxies:
        if p.id == proxy_id:
            p.last_delay_ms = ms


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

    async def one(p: StoredProxy) -> None:
        async with sem:
            try:
                ms = await client.proxy_delay_ms(
                    p.proxy_name,
                    test_url=settings.delay_test_url,
                    timeout_ms=settings.delay_timeout_ms,
                    expected=settings.delay_test_expected,
                )
                p.last_delay_ms = ms
                p.last_sync_error = None
            except MihomoAPIError:
                p.last_delay_ms = None

    await asyncio.gather(*(one(p) for p in store.proxies))
    st.save(store)
    log.info("POST /htmx/test-all: завершено для %d узлов", len(store.proxies))
    return _render_dashboard(
        request,
        settings,
        store,
        message="Проверка задержек завершена.",
        message_kind="info",
    )
