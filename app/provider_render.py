"""Render mihomo file-provider YAML from proxy dicts."""

from __future__ import annotations

import re
from typing import Any

import yaml


def postprocess_yaml_reality_short_ids(text: str) -> str:
    """
    Hex ``short-id`` с буквой ``e`` (напр. ``486e44``) без кавычек в YAML 1.1
    может прочитаться как число в экспоненциальной записи → invalid REALITY short ID в mihomo.
    """
    return re.sub(
        r"^(\s+short-id:\s+)([0-9a-fA-F]*[eE][0-9a-fA-F]+)\s*$",
        r'\1"\2"',
        text,
        flags=re.MULTILINE,
    )


def render_provider_yaml(proxies: list[dict[str, Any]]) -> str:
    """Return YAML document with a top-level ``proxies`` list."""
    doc = {"proxies": proxies}
    text = yaml.safe_dump(
        doc,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return postprocess_yaml_reality_short_ids(text)
