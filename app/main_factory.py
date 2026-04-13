"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.logging_setup import configure_logging
from app.routers import actions, pages
from app.settings import Settings
from app.store_json import StoreJson
from app.sync_service import persist_and_reload


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: rewrite provider YAML from JSON and ask mihomo to reload."""
    settings = Settings()
    store_io = StoreJson(settings.json_store_path)
    store = store_io.load()
    updated, _err = await persist_and_reload(settings, store)
    store_io.save(updated)
    yield


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
