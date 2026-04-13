"""Настройка логирования uvicorn + web4mihomo."""

from __future__ import annotations

import logging
import sys

from app.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Краткий режим — только INFO; подробный — DEBUG и развёрнутый формат."""
    verbose = settings.verbose_app_log
    level = logging.DEBUG if verbose else logging.INFO
    if verbose:
        fmt = "%(asctime)s %(levelname)s [web4mihomo] %(name)s — %(message)s"
    else:
        fmt = "%(asctime)s %(levelname)s [web4mihomo] %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr, force=True)
    # uvicorn / httpx чуть тише в кратком режиме
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
