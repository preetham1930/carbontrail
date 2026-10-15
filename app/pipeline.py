"""Run orchestration: documents -> intake agents -> normalisation -> analysis agents -> result."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.genai import types

from . import config, storage
from .agents.factory import (AnalysisContext, build_analysis_pipeline, build_intake_agent,
                             build_regulation_agent, build_supplier_agent)
from .agents.runtime import parse_json, run_agent
from .cbam.engine import CalcError, calculate, run_scenario, country_comparison
from .checks import run_checks
from .normalize import normalise
from .schemas import DocumentExtraction

log = logging.getLogger("carbontrail")
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "samples" / "golden"


@dataclass
class Doc:
    id: str
    name: str
    mime: str
    data: bytes
    sha: str
    extraction: DocumentExtraction | None = None
    source: str = ""          # "gemini" | "replayed" | "error"
    error: str = ""

    def meta(self) -> dict:
        return {"id": self.id, "name": self.name, "mime": self.mime, "size": len(self.data),
                "source": self.source, "error": self.error,
                "doc_type": self.extraction.doc_type if self.extraction else None,
                "items": len(self.extraction.line_items) if self.extraction else 0}


@dataclass
class Run:
    id: str
    profile: dict
    docs: list[Doc] = field(default_factory=list)
    status: str = "created"
    events: list[dict] = field(default_factory=list)
    listeners: list[asyncio.Queue] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    normalized: Any = None
    findings: list[dict] = field(default_factory=list)
    ctx: AnalysisContext | None = None
    result: dict | None = None
    briefs: dict = field(default_factory=dict)
    supplier_request: dict | None = None
    error: str = ""

    async def emit(self, ev: dict) -> None:
        ev = {"t": round(time.time() - self.created, 2), **ev}
        self.events.append(ev)
        for q in list(self.listeners):
            q.put_nowait(ev)

    def summary(self) -> dict:
        return {
            "id": self.id, "status": self.status, "profile": self.profile, "error": self.error,
            "documents": [d.meta() for d in self.docs],
            "extractions": {d.id: d.extraction.model_dump() for d in self.docs if d.extraction},
            "evidence": {k: v.to_dict() for k, v in (self.normalized.evidence.items() if self.normalized else [])},
            "normalisation_notes": self.normalized.notes if self.normalized else [],
            "findings": self.findings,
            "agent_findings": self.ctx.agent_findings if self.ctx else [],
            "agent_scenarios": self.ctx.agent_scenarios if self.ctx else [],
            "result": self.result,
            "briefs": self.briefs,
            "supplier_request": self.supplier_request,
            "country_comparison": country_comparison("731815"),
        }


RUNS: dict[str, Run] = {}


def new_run(profile: dict) -> Run:
    rid = uuid.uuid4().hex[:12]
    run = Run(id=rid, profile=profile)
    RUNS[rid] = run
    # keep memory bounded
    if len(RUNS) > 200:
        for k in sorted(RUNS, key=lambda k: RUNS[k].created)[:50]:
            RUNS.pop(k, None)
    return run


def add_doc(run: Run, name: str, mime: str, data: bytes) -> Doc:
    d = Doc(id=f"D{len(run.docs) + 1}", name=name, mime=mime, data=data, sha=hashlib.sha256(data).hexdigest())
    run.docs.append(d)
    return d


def _golden(doc: Doc) -> DocumentExtraction | None:
    p = GOLDEN_DIR / f"{doc.sha}.json"
    if p.exists():
        return DocumentExtraction.model_validate_json(p.read_text())
    return None


async def _intake_one(run: Run, doc: Doc, sem: asyncio.Semaphore) -> None:
    async with sem:
        await run.emit({"type": "doc_start", "doc": doc.id, "name": doc.name, "agent": "document_intake_agent"})
        try:
            agent = build_intake_agent()
            parts = [
                types.Part.from_text(text=f"Document file name: {doc.name}. Reporting period of interest: {run.profile.get('period_start')} to {run.profile.get('period_end')}."),
                types.Part.from_bytes(data=doc.data, mime_type=doc.mime),
            ]
            out = await run_agent(agent, parts, label=doc.id)
            raw = out["state"].get("extraction") or parse_json(out["text"])
            doc.extraction = DocumentExtraction.model_validate(raw)
            doc.source = "gemini"
        except Exception as e:  # noqa: BLE001
            log.exception("intake failed for %s", doc.name)
            g = _golden(doc) if config.ALLOW_SAMPLE_REPLAY else None
            if g is not None:
                doc.extraction, doc.source = g, "replayed"
                doc.error = f"Gemini unavailable ({type(e).__name__}); replayed the stored extraction for this sample document."
            else:
                doc.source, doc.error = "error", f"{type(e).__name__}: {e}"[:300]
        ex = doc.extraction
        await run.emit({
            "type": "doc_done", "doc": doc.id, "name": doc.name, "source": doc.source, "error": doc.error,
            "agent": "document_intake_agent",
            "doc_type": ex.doc_type if ex else None,
            "issuer": ex.issuer if ex else None,
            "languages": ex.languages if ex else [],
            "items": [{"kind": i.kind, "description": i.description, "quantity": i.quantity, "unit": i.unit,
                       "cn_code": i.cn_code, "quote": i.source_quote, "page": i.page, "confidence": i.confidence}
                      for i in (ex.line_items if ex else [])],
            "notes": ex.reading_notes if ex else [],
        })


async def analyse(run: Run, docs: list[Doc] | None = None, agents: bool = True) -> None:
    """Full pipeline. If docs is given, only those documents are (re)read."""
    run.status = "running"
    try:
        todo = docs if docs is not None else run.docs
        await run.emit({"type": "stage", "stage": "intake", "text": f"Reading {len(todo)} document(s) with Gemini"})
        sem = asyncio.Semaphore(config.INTAKE_CONCURRENCY)
        await asyncio.gather(*[_intake_one(run, d, sem) for d in todo])
        storage.save_documents(run)

        ok = [d for d in run.docs if d.extraction]
        if not ok:
            raise CalcError("No document could be read.")
        await run.emit({"type": "stage", "stage": "normalise", "text": "Converting units and linking every number to its source"})
        run.normalized = normalise([({"id": d.id, "name": d.name}, d.extraction) for d in ok], run.profile)
        for n in run.normalized.notes:
            await run.emit({"type": "note", "text": n["note"], "doc": n.get("doc")})
        run.findings = run_checks(run.normalized, run.profile)
        run.ctx = AnalysisContext(run.normalized, run.findings)
        # Deterministic result first: the UI never waits on an LLM for numbers.
        run.result = run.ctx.ensure_result()
        await run.emit({"type": "result", "see": run.result["see"], "totals": run.result["totals"]})

        if agents:
            await run.emit({"type": "stage", "stage": "agents", "text": "Scope & quality -> calculation -> advice"})
            try:
                out = await run_agent(build_analysis_pipeline(run.ctx), [types.Part.from_text(
                    text=f"Analyse the CBAM data for {run.profile.get('company', 'the factory')} ({run.profile.get('country', 'India')}), "
                         f"reporting period {run.profile.get('period_start')} to {run.profile.get('period_end')}.")],
                    emit=run.emit)
                st = out["state"]
                run.briefs = {"quality": st.get("quality_review", out["texts"].get("scope_quality_agent", "")),
                              "calculation": st.get("calc_review", out["texts"].get("calculation_agent", "")),
                              "advice": st.get("advice", out["texts"].get("advisor_agent", "")),
                              "source": "gemini"}
            except Exception as e:  # noqa: BLE001
                log.exception("analysis agents failed")
                run.briefs = fallback_briefs(run)
                await run.emit({"type": "warning", "text": f"Analysis agents unavailable ({type(e).__name__}); showing the deterministic summary."})
        run.status = "done"
        await run.emit({"type": "done"})
        storage.save_run(run)
    except Exception as e:  # noqa: BLE001
        log.exception("run failed")
        run.status, run.error = "error", str(e)
        await run.emit({"type": "error", "text": str(e)})


def fallback_briefs(run: Run) -> dict:
    r = run.result or {}
    t = r.get("totals", {})
    see = r.get("see", 0)
    prec = r.get("breakdown", {}).get("precursors", 0)
    return {
        "source": "deterministic",
        "quality": f"{len(run.findings)} automated checks ran; see the findings list.",
        "calculation": f"SEE {see:.3f} tCO2e/t; {prec:.0%} of embedded emissions come from purchased precursors.",
        "advice": (f"### What this means for you\nGross exposure on this period's EU shipments: EUR {t.get('gross_cost_actual_eur', 0):,.0f} "
                   f"with your data vs EUR {t.get('gross_cost_default_eur', 0):,.0f} on EU default values.\n"
                   "### Do this next\n- Ask your wire rod supplier for verified installation-level emissions data."),
    }


async def draft_supplier_request(run: Run, sender: str, supplier: str | None) -> dict:
    ci = run.normalized.calc_input
    p = ci.precursors[0] if ci.precursors else None
    facts = {
        "sender_company": run.profile.get("company"), "sender_name": sender,
        "supplier": supplier or (p.supplier if p else "our wire rod supplier"),
        "material": p.description if p else "wire rod", "cn_code": p.cn if p else "7213",
        "tonnes_supplied": round(p.consumed_t, 1) if p else None,
        "period": f"{ci.period_start} to {ci.period_end}",
        "eu_default_used_now": p.see_actual is None if p else True,
    }
    out = await run_agent(build_supplier_agent(), [types.Part.from_text(text="Facts: " + json.dumps(facts))], emit=run.emit)
    try:
        data = parse_json(out["text"])
        run.supplier_request = {"subject": data.get("subject", ""), "body": data.get("body", ""), "facts": facts}
    except Exception:  # noqa: BLE001
        run.supplier_request = {"subject": "CBAM emissions data for wire rod supplied", "body": out["text"], "facts": facts}
    return run.supplier_request


async def ask(question: str, run: Run | None) -> str:
    case = ""
    if run and run.result:
        r = run.result
        case = (f"\n\nCASE DATA (from the user's analysis): SEE {r['see']:.3f} tCO2e/t; goods "
                + ", ".join(f"{g['cn']} exported {g['export_eu_t']:.1f} t" for g in r["goods"])
                + f"; precursor basis: {', '.join(p['basis'] for p in r['precursors'])}; country {run.profile.get('country')}.")
    out = await run_agent(build_regulation_agent(), [types.Part.from_text(text=question + case)])
    return out["text"]


def scenario(run: Run, params: dict) -> dict:
    return run_scenario(run.normalized.calc_input, params)
