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
from .agents import factory as _factory
from .agents.factory import (AnalysisContext, build_analysis_pipeline, build_intake_agent,
                             build_regulation_agent, build_supplier_agent)
from .agents.runtime import parse_json, run_agent
from .cbam.engine import CalcError, calculate, run_scenario, country_comparison
from .profile_infer import infer_profile
from .checks import run_checks
from .normalize import normalise
from .schemas import DocumentExtraction

log = logging.getLogger("carbontrail")
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "samples" / "golden"


def _models(primary: str) -> list:
    """Primary model first, then the fallbacks. In offline dev mode the scripted model is used as is."""
    if _factory.MODEL_OVERRIDE is not None:
        return [None]
    out = [primary]
    for m in config.MODEL_FALLBACKS:
        if m not in out:
            out.append(m)
    return out


def _quota_error(e: Exception) -> bool:
    t = f"{type(e).__name__} {e}"
    return "ResourceExhausted" in t or "RESOURCE_EXHAUSTED" in t or "429" in t


async def run_with_fallback(build, parts, primary: str, timeout: float, on_retry=None, backoff: float = 10.0, **kw) -> dict:
    """Run an agent; if the model is overloaded (429), unavailable or too slow, rebuild it on the next model.
    A 429 usually means the project's per-minute quota is spent, so wait `backoff` seconds before the next try.
    `build(model)` must return a fresh agent; `on_retry(model, error)` resets any partial state."""
    last: Exception | None = None
    for i, m in enumerate(_models(primary)):
        if i and last is not None and _quota_error(last) and backoff:
            await asyncio.sleep(backoff)
        try:
            return await asyncio.wait_for(run_agent(build(m), parts, **kw), timeout)
        except Exception as e:  # noqa: BLE001
            last = e
            kind = "timed out" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
            log.warning("agent attempt on %s failed (%s): %s", m, kind, str(e)[:200])
            if on_retry and i + 1 < len(_models(primary)):
                await on_retry(m, kind)
    raise last or RuntimeError("no model available")


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
                "items": len(self.extraction.line_items) if self.extraction else 0,
                "set_aside": bool(self.extraction and not self.extraction.is_source_record),
                "set_aside_reason": self.extraction.not_a_record_reason if self.extraction and not self.extraction.is_source_record else None}


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
            parts = [
                types.Part.from_text(text=f"Document file name: {doc.name}. " + (
                    "Reporting period: not known yet; read every date exactly as written." if run.profile.get("auto")
                    else f"Reporting period of interest: {run.profile.get('period_start')} to {run.profile.get('period_end')}.")),
                types.Part.from_bytes(data=doc.data, mime_type=doc.mime),
            ]
            out = await run_with_fallback(build_intake_agent, parts, config.MODEL_INTAKE, config.TIMEOUT_INTAKE, label=doc.id)
            raw = out["state"].get("extraction") or parse_json(out["text"])
            doc.extraction = DocumentExtraction.model_validate(raw)
            doc.source = "gemini"
            if not doc.extraction.is_source_record and config.ALLOW_SAMPLE_REPLAY:
                g = _golden(doc)    # a bundled demo document is always a record: never let a wrong verdict drop it
                if g is not None:
                    doc.extraction, doc.source = g, "replayed"
                    doc.error = "Gemini judged this bundled sample as not a record; replayed its stored extraction."
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
            "set_aside": bool(ex and not ex.is_source_record),
            "set_aside_reason": (ex.not_a_record_reason if ex and not ex.is_source_record else None),
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
        run.profile, pnotes = infer_profile(run.profile, [d.extraction for d in ok])
        for t in pnotes:
            await run.emit({"type": "note", "text": t})
        await run.emit({"type": "stage", "stage": "normalise", "text": "Converting units and linking every number to its source"})
        run.normalized = normalise([({"id": d.id, "name": d.name}, d.extraction) for d in ok], run.profile)
        for n in run.normalized.notes:
            await run.emit({"type": "note", "text": n["note"], "doc": n.get("doc")})
        if not run.normalized.calc_input.goods:
            raise CalcError(_nothing_to_calculate(run.normalized))
        run.findings = run_checks(run.normalized, run.profile)
        run.ctx = AnalysisContext(run.normalized, run.findings)
        # Deterministic result first: the UI never waits on an LLM for numbers.
        run.result = run.ctx.ensure_result()
        await run.emit({"type": "result", "see": run.result["see"], "totals": run.result["totals"]})

        if agents:
            await run.emit({"type": "stage", "stage": "agents", "text": "Scope & quality → calculation → advice"})
            try:
                async def _reset(model, kind):
                    run.ctx.agent_findings.clear()
                    run.ctx.agent_scenarios.clear()
                    await run.emit({"type": "warning", "text": f"Gemini {model} is busy ({kind}); the agents are retrying on a backup Gemini model."})
                out = await run_with_fallback(
                    lambda m: build_analysis_pipeline(run.ctx, m),
                    [types.Part.from_text(
                        text=f"Analyse the CBAM data for {run.profile.get('company', 'the factory')} ({run.profile.get('country', 'India')}), "
                             f"reporting period {run.profile.get('period_start')} to {run.profile.get('period_end')}.")],
                    config.MODEL_REASON, config.TIMEOUT_AGENTS, on_retry=_reset, emit=run.emit)
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


def _nothing_to_calculate(norm) -> str:
    msg = ""
    if norm.set_aside:
        names = "; ".join(f"{x['doc']} ({x['reason']})" for x in norm.set_aside)
        msg = f"These files don't look like factory records, so nothing was taken from them: {names}. "
    msg += ("No production figures were found for the reporting period, so there is nothing to calculate yet. "
            "Upload your production register plus fuel invoices, steel mill statements and export invoices, as photos or PDFs.")
    return msg


def fallback_briefs(run: Run) -> dict:
    r = run.result or {}
    t = r.get("totals", {})
    see = r.get("see", 0)
    prec = r.get("breakdown", {}).get("precursors", 0)
    return {
        "source": "deterministic",
        "quality": f"{len(run.findings)} automated checks ran; see the findings list.",
        "calculation": f"SEE {see:.3f} tCO2e/t; {prec:.0%} of embedded emissions come from purchased precursors.",
        "advice": (f"### What this means for you\nGross CBAM cost of this period's EU shipments: EUR {t.get('gross_cost_actual_eur', 0):,.0f} "
                   f"with your data vs EUR {t.get('gross_cost_default_eur', 0):,.0f} on EU default values, a saving of EUR {t.get('saving_eur') or 0:,.0f}. "
                   + (f"After the free-allocation adjustment the buyers pay an estimated EUR {t['net_cost_actual_eur']:,.0f} vs EUR {t['net_cost_default_eur']:,.0f}.\n"
                      if t.get("net_cost_actual_eur") is not None else "\n")
                   + "### Do this next\n- Ask your wire rod supplier for verified installation-level emissions data."),
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
    try:
        out = await run_with_fallback(build_supplier_agent, [types.Part.from_text(text="Facts: " + json.dumps(facts))],
                                      config.MODEL_REASON, config.TIMEOUT_CHAT * 2, emit=run.emit)
    except Exception:  # noqa: BLE001
        log.exception("supplier agent unavailable; using the template")
        run.supplier_request = _supplier_template(facts)
        return run.supplier_request
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
    try:
        out = await run_with_fallback(build_regulation_agent, [types.Part.from_text(text=question + case)],
                                      config.MODEL_REASON, config.TIMEOUT_CHAT, backoff=2.0)
        if out["text"].strip():
            return out["text"]
    except Exception:  # noqa: BLE001
        log.exception("regulation agent unavailable; answering from the notes")
    return notes_answer(question)


def notes_answer(question: str, k: int = 3) -> str:
    """Fallback when Gemini is unavailable: return the curated notes that best match the question, with their IDs."""
    import re
    from .agents.factory import _KNOWLEDGE
    notes = re.findall(r"^\[(K\d+)\]\s*(.+)$", _KNOWLEDGE, re.M)
    stop = set("a an the is are do does did my our your i we to of in on for and or what when how which who why can will be it this that with by at from as".split())
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in stop and len(w) > 2}
    related = {"pay": "price certificate certificates declaration sales surrender", "paid": "price certificate declaration",
               "cost": "price certificate default mark-up", "when": "due date 2027 from", "deadline": "due date 2027",
               "default": "mark-up default values", "verify": "verified verifier accredited", "verified": "verifier accredited"}
    for w in list(words):
        words |= set(related.get(w, "").split())
    def score(text):
        t = text.lower()
        return sum(2 if w in t.split() else 1 for w in words if w in t)
    ranked = sorted(notes, key=lambda n: score(n[1]), reverse=True)
    best = [n for n in ranked[:k] if score(n[1]) > 0] or ranked[:1]
    body = "\n".join(f"- {re.sub(r'Source:.*$', '', text).strip()} [{nid}]" for nid, text in best)
    return ("Gemini is busy right now, so here are the regulation notes that match your question most closely:\n"
            + body + "\nAsk again in a minute for a full answer.")


def _supplier_template(facts: dict) -> dict:
    t = facts.get("tonnes_supplied")
    body = (f"Dear {facts.get('supplier') or 'Sir/Madam'},\n\n"
            f"We are preparing EU CBAM emissions data for our exports. For the {facts.get('material', 'wire rod')} (CN {facts.get('cn_code', '7213')}) "
            f"you supplied to us{f' ({t} t)' if t else ''} in the period {facts.get('period')}, could you please share, for the producing installation:\n"
            "1. The specific embedded emissions (direct) in tCO2e per tonne, calculated with the EU CBAM method (IR (EU) 2025/2547).\n"
            "2. The installation name, location and production route.\n"
            "3. Whether the figure has been verified by an accredited verifier, with the verification report if available.\n"
            "4. Any carbon price effectively paid for these emissions.\n\n"
            "Without your figure our buyers must use the EU default value plus a mark-up, which raises their CBAM cost.\n\n"
            f"Kind regards,\n{facts.get('sender_name') or ''}\n{facts.get('sender_company') or ''}")
    return {"subject": f"CBAM emissions data for {facts.get('material', 'wire rod')} supplied, {facts.get('period')}",
            "body": body, "facts": facts, "source": "template"}


def scenario(run: Run, params: dict) -> dict:
    return run_scenario(run.normalized.calc_input, params)
