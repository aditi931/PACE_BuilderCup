"""Run history: every decision is stored with its full evidence, so it can be audited or shared later.

RUN_STORE=firestore  -> Google Cloud Firestore (default on Cloud Run)
RUN_STORE=local      -> JSON files under CACHE_DIR/runs (laptop)
RUN_STORE=off        -> not stored
The full result is gzip-compressed into one field to stay well under Firestore's 1 MB document limit.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import uuid
from pathlib import Path

COLLECTION = "trend2line_runs"


def _summary(result: dict, run_id: str) -> dict:
    d = result["decision"]
    return {"id": run_id, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "trend": result["trend"].get("article"), "market": d["market"], "verdict": d["verdict"],
            "mode": result["mode"], "company": result.get("company", ""),
            "deadline_go_date": d.get("deadline_go_date"), "days_to_shelf": d.get("days_to_shelf")}


class LocalStore:
    kind = "local"

    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, result: dict) -> str:
        run_id = uuid.uuid4().hex[:12]
        (self.dir / f"{run_id}.json.gz").write_bytes(gzip.compress(json.dumps(result, default=str).encode()))
        (self.dir / f"{run_id}.meta.json").write_text(json.dumps(_summary(result, run_id)), encoding="utf-8")
        return run_id

    def list(self, limit: int = 20) -> list[dict]:
        metas = [json.loads(p.read_text(encoding="utf-8")) for p in self.dir.glob("*.meta.json")]
        return sorted(metas, key=lambda m: m["created_at"], reverse=True)[:limit]

    def get(self, run_id: str) -> dict | None:
        p = self.dir / f"{run_id}.json.gz"
        if not run_id.isalnum() or not p.exists():
            return None
        return json.loads(gzip.decompress(p.read_bytes()))


class FirestoreStore:
    kind = "firestore"

    def __init__(self, project: str, client=None):
        if client is None:
            from google.cloud import firestore  # lazily: only on Cloud Run / when configured
            client = firestore.Client(project=project)
        self.db = client

    def save(self, result: dict) -> str:
        run_id = uuid.uuid4().hex[:12]
        blob = gzip.compress(json.dumps(result, default=str).encode())
        self.db.collection(COLLECTION).document(run_id).set({**_summary(result, run_id), "result_gz": blob})
        return run_id

    def list(self, limit: int = 20) -> list[dict]:
        from google.cloud import firestore
        q = self.db.collection(COLLECTION).order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
        return [{k: v for k, v in doc.to_dict().items() if k != "result_gz"} for doc in q.stream()]

    def get(self, run_id: str) -> dict | None:
        if not run_id.isalnum():
            return None
        doc = self.db.collection(COLLECTION).document(run_id).get()
        if not doc.exists:
            return None
        return json.loads(gzip.decompress(doc.to_dict()["result_gz"]))


_STORE = None


def get_store(settings):
    global _STORE
    if settings.run_store == "off":
        return None
    if _STORE is None or _STORE.kind != settings.run_store:
        _STORE = (FirestoreStore(settings.gcp_project) if settings.run_store == "firestore"
                  else LocalStore(settings.cache_dir / "runs"))
    return _STORE
