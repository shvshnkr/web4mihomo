"""Full-page routes (Jinja2)."""

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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: SettingsDep):
    if settings.ui_password and not request.session.get("web4_auth"):
        return RedirectResponse(
            url=f"/login?next=/",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    store = _store(settings).load()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "settings": settings,
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
