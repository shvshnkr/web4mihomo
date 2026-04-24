"""Full-page routes (Jinja2)."""

import json
from datetime import datetime, timezone

from pathlib import Path

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response
from fastapi.templating import Jinja2Templates

from app.deps import SettingsDep
from app.models import LoginForm
from app.store_json import StoreJson

base = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(base / "templates"))

router = APIRouter()


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Браузеры запрашивают по умолчанию; без маршрута — лишние 404 в логе."""
    return Response(status_code=204)


def _store(settings: SettingsDep) -> StoreJson:
    return StoreJson(settings.json_store_path)


def _effective_settings(settings: SettingsDep, store) -> SettingsDep:
    updates: dict[str, object] = {}
    if store.ui_auto_filter_enabled is not None:
        updates["auto_filter_enabled"] = store.ui_auto_filter_enabled
    if store.ui_auto_filter_max_delay_ms is not None:
        updates["auto_filter_max_delay_ms"] = store.ui_auto_filter_max_delay_ms
    if getattr(store, "ui_auto_filter_source", None) is not None:
        updates["auto_filter_source"] = store.ui_auto_filter_source
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _dbg(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "41d724",
            "runId": "ui-f5-forgets",
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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: SettingsDep):
    if settings.ui_password and not request.session.get("web4_auth"):
        return RedirectResponse(
            url=f"/login?next=/",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    store = _store(settings).load()
    effective_settings = _effective_settings(settings, store)
    # region agent log
    _dbg(
        "H20",
        "app/routers/pages.py:index",
        "index_render_values",
        {
            "env_enabled": settings.auto_filter_enabled,
            "env_max_delay": settings.auto_filter_max_delay_ms,
            "env_source": settings.auto_filter_source,
            "store_enabled": store.ui_auto_filter_enabled,
            "store_max_delay": store.ui_auto_filter_max_delay_ms,
            "store_source": store.ui_auto_filter_source,
            "effective_enabled": effective_settings.auto_filter_enabled,
            "effective_max_delay": effective_settings.auto_filter_max_delay_ms,
            "effective_source": effective_settings.auto_filter_source,
        },
    )
    # endregion
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "settings": effective_settings,
            "store": store,
        },
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, settings: SettingsDep):
    if not settings.ui_password:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    next_url = request.query_params.get("next") or "/"
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "settings": settings, "next_url": next_url},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    settings: SettingsDep,
    password: str = Form(""),
    next: str = Form("/"),
):
    if not settings.ui_password:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    form = LoginForm.from_form(password)
    if form.password != settings.ui_password:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "settings": settings,
                "next_url": next or "/",
                "error": "Неверный пароль",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    request.session["web4_auth"] = True
    nxt = next or "/"
    if not nxt.startswith("/"):
        nxt = "/"
    return RedirectResponse(nxt, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request, settings: SettingsDep):
    request.session.clear()
    if settings.ui_password:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
