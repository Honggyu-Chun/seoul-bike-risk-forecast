"""Backend contract client; incremental disk cache is independent of backend DB."""
from datetime import timedelta
import json
from pathlib import Path

import httpx

from .common import now, save_json, stamp
from .store import Store


class HTTPBackend:
    def __init__(self, base_url: str, token: str, cache: Store, state_path: Path, transport=None):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), headers={"Authorization": "Bearer " + token},
                                   timeout=20, transport=transport, follow_redirects=False)
        self.cache, self.state_path = cache, state_path

    def sync(self, initial_hours=170):
        state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        total = 0
        for table in ("stations", "observations", "forecasts"):
            previous = state.get(table, {})
            cursor, watermark = previous.get("cursor", 0), None
            start = previous.get("start") or stamp(now() - timedelta(hours=initial_hours))
            while True:
                params = {"cursor": cursor, "limit": 10000}
                if table != "stations":
                    params["start"] = start
                if watermark is not None:
                    params["watermark"] = watermark
                r = self.client.get("/internal/ai-data/v1/" + table, params=params)
                r.raise_for_status()
                page = r.json()
                if page["next_cursor"] < cursor or (page["has_more"] and page["next_cursor"] <= cursor):
                    raise ValueError("Non-monotonic backend cursor")
                self.cache.import_page(table, page)
                total += len(page["items"])
                cursor, watermark = page["next_cursor"], page["watermark"]
                state[table] = {"cursor": cursor, "start": start, "synced_at": stamp(now())}
                save_json(self.state_path, state)
                if not page["has_more"]:
                    break
        return {"items_synced": total, "synced_at": stamp(now())}

    def close(self):
        self.client.close()
