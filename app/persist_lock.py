"""Shared async lock for persist/load-save critical sections."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

_persist_cycle_lock = asyncio.Lock()


@asynccontextmanager
async def persist_cycle_lock() -> AsyncIterator[None]:
    """
    Serialize store persist cycles across request handlers and background loop.

    This guards short critical sections that do ``load -> merge -> persist -> save``
    to avoid stale snapshots overwriting newer manual changes.
    """
    async with _persist_cycle_lock:
        yield
