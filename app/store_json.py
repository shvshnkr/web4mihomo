"""JSON persistence for stored proxies/subscriptions (atomic writes)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.models import ProxyStore, StoredProxy, StoredSubscription


class StoreJson:
    """Read/write ``ProxyStore`` to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ProxyStore:
        if not self.path.is_file():
            return ProxyStore()
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            return ProxyStore()
        data = json.loads(raw)
        return ProxyStore.model_validate(data)

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
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def upsert(self, store: ProxyStore, item: StoredProxy) -> ProxyStore:
        items = [p for p in store.proxies if p.id != item.id]
        items.append(item)
        store = ProxyStore(proxies=items)
        self.save(store)
        return store

    def remove(self, store: ProxyStore, proxy_id: str) -> ProxyStore:
        store = ProxyStore(
            proxies=[p for p in store.proxies if p.id != proxy_id],
            subscriptions=store.subscriptions,
        )
        self.save(store)
        return store

    def remove_by_subscription(self, store: ProxyStore, subscription_id: str) -> ProxyStore:
        store = ProxyStore(
            proxies=[p for p in store.proxies if p.subscription_id != subscription_id],
            subscriptions=store.subscriptions,
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
        store = ProxyStore(proxies=store.proxies, subscriptions=subs)
        self.save(store)
        return store

    def remove_subscription(self, store: ProxyStore, subscription_id: str) -> ProxyStore:
        store = ProxyStore(
            proxies=[p for p in store.proxies if p.subscription_id != subscription_id],
            subscriptions=[s for s in store.subscriptions if s.id != subscription_id],
        )
        self.save(store)
        return store

    def subscription_by_id(self, store: ProxyStore, subscription_id: str) -> StoredSubscription | None:
        for s in store.subscriptions:
            if s.id == subscription_id:
                return s
        return None
