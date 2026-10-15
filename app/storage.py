"""Best-effort persistence to Cloud Storage (documents) and Firestore (results).

Both are optional and never block a run: if they are not configured or fail,
CarbonTrail keeps working from memory.
"""
from __future__ import annotations

import logging

from . import config

log = logging.getLogger("carbontrail.storage")
_gcs = None
_fs = None


def _bucket():
    global _gcs
    if not config.GCS_BUCKET:
        return None
    if _gcs is None:
        from google.cloud import storage  # lazy import
        _gcs = storage.Client().bucket(config.GCS_BUCKET)
    return _gcs


def _db():
    global _fs
    if not config.USE_FIRESTORE:
        return None
    if _fs is None:
        from google.cloud import firestore  # lazy import
        _fs = firestore.Client()
    return _fs


def save_documents(run) -> None:
    try:
        b = _bucket()
        if b is None:
            return
        for d in run.docs:
            blob = b.blob(f"runs/{run.id}/{d.id}-{d.name}")
            if not blob.exists():
                blob.upload_from_string(d.data, content_type=d.mime)
    except Exception:  # noqa: BLE001
        log.warning("GCS upload skipped", exc_info=True)


def save_run(run) -> None:
    try:
        db = _db()
        if db is None:
            return
        r = run.result or {}
        db.collection("runs").document(run.id).set({
            "company": run.profile.get("company"), "country": run.profile.get("country"),
            "period": [run.profile.get("period_start"), run.profile.get("period_end")],
            "see": r.get("see"), "totals": r.get("totals"), "status": run.status,
            "documents": [d.meta() for d in run.docs], "created": run.created,
        })
    except Exception:  # noqa: BLE001
        log.warning("Firestore save skipped", exc_info=True)
