"""Evidence registry: every tool output gets a stable ID that claims must cite."""
from typing import Any


class EvidenceRegistry:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def record(self, source: str, query: str, payload: dict) -> str:
        eid = f"ev_{len(self.items):03d}"
        self.items.append({"id": eid, "source": source, "query": query, "payload": payload})
        return eid

    def get(self, eid: str) -> dict | None:
        return next((e for e in self.items if e["id"] == eid), None)

    def lookup(self, eid: str, field: str) -> Any:
        """Resolve a dot path such as 'capture.median' inside an evidence payload."""
        ev = self.get(eid)
        if ev is None:
            raise KeyError(f"unknown evidence id {eid}")
        cur: Any = ev["payload"]
        for part in field.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                raise KeyError(f"{eid} has no field '{field}'")
        return cur

    def for_prompt(self, skip_fields: tuple[str, ...] = ("series_weekly",)) -> list[dict]:
        out = []
        for e in self.items:
            payload = e["payload"]
            if isinstance(payload, dict):
                payload = {k: v for k, v in payload.items() if k not in skip_fields}
            out.append({"id": e["id"], "source": e["source"], "payload": payload})
        return out
