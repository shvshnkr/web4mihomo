"""JSON persistence for stored proxies/subscriptions (atomic writes)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.models import ProxyStore, StoredProxy, StoredSubscription


class StoreJson:
    """Read/write ``ProxyStore`` to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _dbg(self, hypothesis_id: str, location: str, message: str, data: dict) -> None:
        # region agent log
        try:
            payload = {
                "sessionId": "41d724",
                "runId": "dual-provider-state",
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

    def load(self) -> ProxyStore:
        if not self.path.is_file():
            self._dbg("H1", "app/store_json.py:load", "store_file_missing", {"path": str(self.path)})
            return ProxyStore()
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            self._dbg("H1", "app/store_json.py:load", "store_file_empty", {"path": str(self.path)})
            return ProxyStore()
        data = json.loads(raw)
        store = ProxyStore.model_validate(data)
        self._dbg(
            "H1",
            "app/store_json.py:load",
            "store_loaded",
            {"path": str(self.path), "proxies": len(store.proxies), "subscriptions": len(store.subscriptions)},
        )
        return store

    def save(self, store: ProxyStore) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = store.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(
            prefix=".my_vless_",
            suffix=".json.tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self._dbg(
                "H2",
                "app/store_json.py:save",
                "store_saved",
                {"path": str(self.path), "proxies": len(store.proxies), "subscriptions": len(store.subscriptions)},
            )
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def upsert(self, store: ProxyStore, item: StoredProxy) -> ProxyStore:
        items = [p for p in store.proxies if p.id != item.id]
        items.append(item)
        store = ProxyStore(
            proxies=items,
            subscriptions=store.subscriptions,
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        )
        self.save(store)
        return store

    def remove(self, store: ProxyStore, proxy_id: str) -> ProxyStore:
        store = ProxyStore(
            proxies=[p for p in store.proxies if p.id != proxy_id],
            subscriptions=store.subscriptions,
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        )
        self.save(store)
        return store

    def remove_by_subscription(self, store: ProxyStore, subscription_id: str) -> ProxyStore:
        store = ProxyStore(
            proxies=[p for p in store.proxies if p.subscription_id != subscription_id],
            subscriptions=store.subscriptions,
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        )
        self.save(store)
        return store

    def by_id(self, store: ProxyStore, proxy_id: str) -> StoredProxy | None:
        for p in store.proxies:
            if p.id == proxy_id:
                return p
        return None

    def upsert_subscription(self, store: ProxyStore, item: StoredSubscription) -> ProxyStore:
        subs = [s for s in store.subscriptions if s.id != item.id]
        subs.append(item)
        store = ProxyStore(
            proxies=store.proxies,
            subscriptions=subs,
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        )
        self.save(store)
        return store

    def remove_subscription(self, store: ProxyStore, subscription_id: str) -> ProxyStore:
        store = ProxyStore(
            proxies=[p for p in store.proxies if p.subscription_id != subscription_id],
            subscriptions=[s for s in store.subscriptions if s.id != subscription_id],
            ui_auto_filter_enabled=store.ui_auto_filter_enabled,
            ui_auto_filter_max_delay_ms=store.ui_auto_filter_max_delay_ms,
        )
        self.save(store)
        return store

    def subscription_by_id(self, store: ProxyStore, subscription_id: str) -> StoredSubscription | None:
        for s in store.subscriptions:
            if s.id == subscription_id:
                return s
        return None
