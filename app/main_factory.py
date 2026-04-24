"""FastAPI application factory."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.logging_setup import configure_logging
from app.mihomo_client import MihomoAPIError, MihomoClient
from app.routers import actions, pages
from app.settings import Settings
from app.store_json import StoreJson
from app.sync_service import apply_auto_filter_policy, materialize_subscription_proxies, persist_and_reload

log = logging.getLogger("web4mihomo.lifecycle")


async def _auto_refresh_loop(settings: Settings) -> None:
    """Periodic subscriptions refresh and auto-filter loop."""
    store_io = StoreJson(settings.json_store_path)
    client = MihomoClient(settings)

    def effective_runtime_settings(base: Settings, store) -> Settings:
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
            return base
        return base.model_copy(update=updates)

    def extract_mihomo_delay_map(payload: dict[str, Any]) -> dict[str, int | None]:
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
            if node.get("alive") is False:
                out[name] = None
        return out

    async def one_delay_check(p, sem: asyncio.Semaphore, run_settings: Settings) -> None:
        async with sem:
            try:
                p.last_delay_ms = await client.proxy_delay_ms(
                    p.proxy_name,
                    test_url=run_settings.auto_filter_probe_url,
                    timeout_ms=run_settings.delay_timeout_ms,
                    expected=run_settings.delay_test_expected,
                )
                p.last_delay_error = None
            except MihomoAPIError:
                p.last_delay_ms = None
                p.last_delay_error = "Delay failed"
            except Exception as e:
                p.last_delay_ms = None
                p.last_delay_error = f"Delay error: {type(e).__name__}"
                log.debug("auto-refresh delay check error on %s: %s", p.proxy_name, e)

    while True:
        store = store_io.load()
        run_settings = effective_runtime_settings(settings, store)
        if run_settings.auto_filter_enabled:
            interval = run_settings.auto_filter_recheck_interval_sec
        elif settings.subscriptions_auto_refresh_interval_sec > 0:
            interval = settings.subscriptions_auto_refresh_interval_sec
        else:
            interval = 30

        await asyncio.sleep(interval)
        try:
            store = store_io.load()
            run_settings = effective_runtime_settings(settings, store)
            if run_settings.auto_filter_enabled:
                store_for_test = materialize_subscription_proxies(store, apply_excludes=False)
                sem = asyncio.Semaphore(run_settings.test_all_concurrency)
                await asyncio.gather(*(one_delay_check(p, sem, run_settings) for p in store_for_test.proxies))
                mihomo_delay_map: dict[str, int | None] | None = None
                if run_settings.auto_filter_source in {"mihomo", "hybrid"}:
                    try:
                        payload = await client.get_proxies_payload()
                        mihomo_delay_map = extract_mihomo_delay_map(payload)
                    except Exception as e:
                        log.warning("auto-refresh: failed to load mihomo health map: %s", e)
                store_for_test = apply_auto_filter_policy(
                    store_for_test,
                    run_settings,
                    mihomo_delay_map=mihomo_delay_map,
                )
                updated, err = await persist_and_reload(run_settings, store_for_test, refresh_subscriptions=True)
            elif settings.subscriptions_auto_refresh_interval_sec > 0:
                updated, err = await persist_and_reload(run_settings, store, refresh_subscriptions=True)
            else:
                continue
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
    should_start_refresh_loop = (
        settings.subscriptions_auto_refresh_interval_sec > 0
        or settings.auto_filter_enabled
        or bool(store.ui_auto_filter_enabled)
    )
    if should_start_refresh_loop:
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
