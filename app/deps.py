"""FastAPI dependencies: settings and optional UI auth."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.settings import Settings


def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]


def _is_public_path(path: str) -> bool:
    return path.startswith("/static/") or path in ("/login", "/favicon.ico")


def require_ui_session_htmx(request: Request, settings: SettingsDep) -> None:
    """Require session; for HTMX requests signal full redirect via ``HX-Redirect``."""
    if not settings.ui_password:
        return
    if _is_public_path(request.url.path):
        return
    if request.session.get("web4_auth") is True:
        return
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"HX-Redirect": f"/login?next={next_path}"},
        detail="login required",
    )
