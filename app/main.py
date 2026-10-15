"""CarbonTrail web service (FastAPI on Cloud Run)."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from . import config, exports, pipeline
from .cbam.engine import REF, CalcError, country_comparison

logging.basicConfig(level=logging.INFO)
ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT.parent / "samples"

app = FastAPI(title="CarbonTrail", version="1.0")
# Compress the page, scripts and the vendored 3D library (SSE streams are excluded by Starlette).
app.add_middleware(GZipMiddleware, minimum_size=1024)

if os.getenv("CT_DEV_SCRIPTED") == "1":  # offline UI development only
    from .agents import factory as _factory
    from .agents.devmodel import ScriptedLlm
    _factory.MODEL_OVERRIDE = ScriptedLlm()

DEMO_PROFILE = {
    "company": "Verkam Fasteners Pvt. Ltd. (fictional)",
    "installation": "Plant 1, Focal Point Phase VIII",
    "city": "Ludhiana, Punjab",
    "country": "India",
    "year": 2026,
    "period_start": "2026-04-01",
    "period_end": "2026-06-30",
    "price_quarter": "2026-Q3",
    "contact_name": "Operations Head",
}

ALLOWED = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


def _get(run_id: str) -> pipeline.Run:
    run = pipeline.RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found (runs are kept in memory; start a new one).")
    return run


def _sample_manifest() -> list[dict]:
    m = SAMPLES / "manifest.json"
    return json.loads(m.read_text()) if m.exists() else []


@app.get("/api/health")
def health():
    return {"ok": True, "models": {"intake": config.MODEL_INTAKE, "reason": config.MODEL_REASON},
            "project": config.PROJECT or "(from environment)", "location": config.LOCATION}


@app.get("/api/samples")
def samples():
    return {"profile": DEMO_PROFILE, "documents": _sample_manifest()}


@app.get("/api/reference")
def reference():
    return {"price": REF["certificate_price_eur"], "markup": REF["default_markup"],
            "bolts": country_comparison("731815"), "nuts": country_comparison("731816"),
            "regulations": REF["regulations"]}


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, str, bytes]]:
    out = []
    for f in files or []:
        data = await f.read()
        if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} is larger than {config.MAX_UPLOAD_MB} MB")
        mime = f.content_type or mimetypes.guess_type(f.filename or "")[0] or ""
        if mime not in ALLOWED:
            raise HTTPException(415, f"{f.filename}: upload PDF, JPG, PNG or WEBP")
        out.append((f.filename or "document", mime, data))
    return out


@app.post("/api/runs")
async def create_run(background: BackgroundTasks, profile: str = Form("{}"), use_samples: bool = Form(False),
                     files: list[UploadFile] = File(default=[])):
    try:
        prof = {**DEMO_PROFILE, **json.loads(profile or "{}")}
    except json.JSONDecodeError:
        raise HTTPException(400, "profile must be JSON")
    uploads = await _read_uploads(files)
    if use_samples:
        for d in _sample_manifest():
            if d.get("stage", "initial") != "initial":
                continue
            p = SAMPLES / d["file"]
            uploads.append((d["file"], d["mime"], p.read_bytes()))
    if not uploads:
        raise HTTPException(400, "Add at least one document or use the demo documents.")
    run = pipeline.new_run(prof)
    for name, mime, data in uploads:
        pipeline.add_doc(run, name, mime, data)
    background.add_task(pipeline.analyse, run)
    return {"id": run.id, "documents": [d.meta() for d in run.docs]}


@app.post("/api/runs/{run_id}/documents")
async def add_documents(run_id: str, background: BackgroundTasks, sample: str = Form(""),
                        files: list[UploadFile] = File(default=[])):
    run = _get(run_id)
    if run.status == "running":
        raise HTTPException(409, "Analysis still running")
    uploads = await _read_uploads(files)
    if sample:
        d = next((x for x in _sample_manifest() if x["file"] == sample), None)
        if not d:
            raise HTTPException(404, "Unknown sample")
        uploads.append((d["file"], d["mime"], (SAMPLES / d["file"]).read_bytes()))
    if not uploads:
        raise HTTPException(400, "No document")
    new = [pipeline.add_doc(run, n, m, b) for n, m, b in uploads]
    run.status = "running"
    background.add_task(pipeline.analyse, run, new)
    return {"id": run.id, "documents": [d.meta() for d in new]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return JSONResponse(_get(run_id).summary())


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str, request: Request, since: int = 0):
    run = _get(run_id)
    q: asyncio.Queue = asyncio.Queue()
    backlog = list(run.events[since:])
    run.listeners.append(q)

    async def gen():
        try:
            for ev in backlog:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            if q in run.listeners:
                run.listeners.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/docs/{doc_id}")
def get_doc(run_id: str, doc_id: str):
    run = _get(run_id)
    d = next((x for x in run.docs if x.id == doc_id), None)
    if not d:
        raise HTTPException(404)
    return Response(d.data, media_type=d.mime, headers={"Cache-Control": "private, max-age=3600"})


class ScenarioReq(BaseModel):
    precursor_see: float | None = None
    fuel_switch_to: str | None = None
    yield_gain_pct: float | None = None
    renewable_electricity_pct: float | None = None
    year: int | None = None


@app.post("/api/runs/{run_id}/scenario")
def scenario(run_id: str, req: ScenarioReq):
    run = _get(run_id)
    if not run.normalized:
        raise HTTPException(409, "Run not analysed yet")
    try:
        s = pipeline.scenario(run, {k: v for k, v in req.model_dump().items() if v not in (None, "", 0)})
    except CalcError as e:
        raise HTTPException(400, str(e))
    r = s["result"]
    return {"applied": s["applied"], "base": s["base"], "scenario": s["scenario"], "delta_see": s["delta_see"],
            "delta_cost_eur": s["delta_cost_eur"], "breakdown": r["breakdown"], "attributed_direct_t": r["attributed_direct_t"],
            "precursor_emissions_t": r["precursor_emissions_t"]}


class SupplierReq(BaseModel):
    sender: str = "Operations Head"
    supplier: str | None = None


@app.post("/api/runs/{run_id}/supplier-request")
async def supplier_request(run_id: str, req: SupplierReq):
    run = _get(run_id)
    if not run.normalized:
        raise HTTPException(409, "Run not analysed yet")
    try:
        return await pipeline.draft_supplier_request(run, req.sender, req.supplier)
    except Exception as e:  # noqa: BLE001
        logging.exception("supplier agent failed")
        raise HTTPException(502, f"Supplier agent unavailable: {type(e).__name__}")


class AskReq(BaseModel):
    question: str
    run_id: str | None = None


@app.post("/api/ask")
async def ask(req: AskReq):
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(400, "Empty question")
    run = pipeline.RUNS.get(req.run_id) if req.run_id else None
    try:
        return {"answer": await pipeline.ask(q[:1500], run)}
    except Exception as e:  # noqa: BLE001
        logging.exception("regulation agent failed")
        raise HTTPException(502, f"Regulation agent unavailable: {type(e).__name__}")


@app.get("/api/runs/{run_id}/export/data-pack.xlsx")
def export_xlsx(run_id: str):
    run = _get(run_id)
    if not run.result:
        raise HTTPException(409, "No result yet")
    return Response(exports.data_pack_xlsx(run), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="carbontrail-data-pack-{run.id}.xlsx"'})


@app.get("/api/runs/{run_id}/export/evidence.pdf")
def export_pdf(run_id: str):
    run = _get(run_id)
    if not run.result:
        raise HTTPException(409, "No result yet")
    return Response(exports.evidence_pdf(run), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="carbontrail-evidence-pack-{run.id}.pdf"'})


app.mount("/samples", StaticFiles(directory=str(SAMPLES)), name="samples")
app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True), name="static")
