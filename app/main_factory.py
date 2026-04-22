"""FastAPI application factory."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.logging_setup import configure_logging
from app.routers import actions, pages
from app.settings import Settings
from app.store_json import StoreJson
from app.sync_service import persist_and_reload

log = logging.getLogger("web4mihomo.lifecycle")


async def _auto_refresh_loop(settings: Settings) -> None:
    """Periodic subscriptions refresh loop."""
    interval = settings.subscriptions_auto_refresh_interval_sec
    if interval <= 0:
        return
    store_io = StoreJson(settings.json_store_path)
    while True:
        await asyncio.sleep(interval)
        try:
            store = store_io.load()
            updated, err = await persist_and_reload(settings, store, refresh_subscriptions=True)
            store_io.save(updated)
            if err:
                log.warning("auto-refresh: mihomo reload error: %s", err)
        except Exception as e:
            log.exception("auto-refresh: unexpected error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: rewrite provider YAML from JSON and ask mihomo to reload."""
    settings = Settings()
    store_io = StoreJson(settings.json_store_path)
    store = store_io.load()
    updated, _err = await persist_and_reload(
        settings,
        store,
        refresh_subscriptions=settings.subscriptions_refresh_on_startup,
    )
    store_io.save(updated)
    refresh_task: asyncio.Task[None] | None = None
    if settings.subscriptions_auto_refresh_interval_sec > 0:
        refresh_task = asyncio.create_task(_auto_refresh_loop(settings))
    yield
    if refresh_task:
        refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await refresh_task


def create_app() -> FastAPI:
    settings = Settings()
    configure_logging(settings)
    app = FastAPI(title="web4mihomo", lifespan=lifespan)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=False,
    )

    base = Path(__file__).resolve().parent.parent
    static_dir = base / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(pages.router)
    app.include_router(actions.router)

    app.state.settings = settings

    return app
