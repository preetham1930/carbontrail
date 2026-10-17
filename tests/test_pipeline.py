"""End-to-end test of the ADK wiring with a scripted model (no network).

The scripted model plays Gemini: it returns the stored extraction for each
sample document and calls the analysis tools in a realistic order. This
verifies agents, tools, event streaming, normalisation, checks, the
calculation and the exports work together.
"""
import asyncio
import hashlib
import json
from pathlib import Path
from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm

from app import pipeline
from app.agents import factory
from app.main import DEMO_PROFILE
from app import exports
from app.agents.devmodel import ScriptedLlm

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
GOLD = SAMPLES / "golden"

@pytest.fixture(autouse=True)
def scripted():
    factory.MODEL_OVERRIDE = ScriptedLlm()
    yield
    factory.MODEL_OVERRIDE = None


def _load_initial(run):
    man = json.loads((SAMPLES / "manifest.json").read_text())
    for d in man:
        if d["stage"] == "initial":
            pipeline.add_doc(run, d["file"], d["mime"], (SAMPLES / d["file"]).read_bytes())


def test_full_pipeline():
    run = pipeline.new_run(dict(DEMO_PROFILE))
    _load_initial(run)
    asyncio.run(pipeline.analyse(run))
    assert run.status == "done", run.error
    r = run.result
    # Same answer as the hand-checked worked example with EU default for the precursor
    assert round(r["activity_level_t"], 3) == 244.0
    assert round(r["see"], 4) == 5.3584
    assert round(r["totals"]["export_eu_t"], 3) == 160.0
    codes = {f["code"] for f in run.findings}
    assert "precursor_default" in codes and "non_eu_export" in codes and "dg_excluded" in codes
    assert "electricity_gap" in codes and "low_confidence" in codes
    types_ = [e["type"] for e in run.events]
    assert "doc_done" in types_ and "tool_call" in types_ and "done" in types_
    tools = {e["tool"] for e in run.events if e["type"] == "tool_call"}
    assert {"get_activity_data", "calculate_embedded_emissions", "simulate_scenario"} <= tools
    assert run.briefs.get("source") == "gemini"
    assert run.ctx.agent_findings and run.ctx.agent_scenarios
    # Exports render
    assert exports.data_pack_xlsx(run)[:2] == b"PK"
    assert exports.evidence_pdf(run)[:4] == b"%PDF"

    # Stage 2: the supplier's verified data arrives
    man = json.loads((SAMPLES / "manifest.json").read_text())
    sup = next(d for d in man if d["stage"] == "supplier_reply")
    new = pipeline.add_doc(run, sup["file"], sup["mime"], (SAMPLES / sup["file"]).read_bytes())
    asyncio.run(pipeline.analyse(run, [new]))
    assert run.status == "done", run.error
    assert round(run.result["see"], 4) == 3.2514
    assert run.result["totals"]["saving_eur"] > 34500

    draft = asyncio.run(pipeline.draft_supplier_request(run, "Ops Head", None))
    assert draft["subject"]
    ans = asyncio.run(pipeline.ask("Does electricity count?", run))
    assert "[K3]" in ans


def test_replay_when_model_fails(monkeypatch):
    class Broken(BaseLlm):
        model: str = "broken"

        async def generate_content_async(self, llm_request, stream=False):
            raise RuntimeError("quota")
            yield  # pragma: no cover

    factory.MODEL_OVERRIDE = Broken()
    run = pipeline.new_run(dict(DEMO_PROFILE))
    _load_initial(run)
    asyncio.run(pipeline.analyse(run))
    assert run.status == "done"
    assert all(d.source == "replayed" for d in run.docs)
    assert run.briefs["source"] == "deterministic"
    assert round(run.result["see"], 4) == 5.3584


def test_supplier_indirect_value_is_ignored():
    """A mill declaration that also states an indirect (electricity) value must not overwrite the direct SEE."""
    from app.normalize import normalise
    from app.schemas import DocumentExtraction
    from app.cbam.engine import calculate
    man = json.loads((SAMPLES / "manifest.json").read_text())
    docs = []
    for i, d in enumerate(man):
        ex = DocumentExtraction(**json.loads((GOLD / f"{d['sha256']}.json").read_text()))
        if d["stage"] == "supplier_reply":
            direct = ex.line_items[0]
            indirect = direct.model_copy(update={"quantity": 0.31, "description": "Wire rod specific embedded emissions (indirect)",
                                                 "source_quote": "Indirect emissions 0.31 tCO2e per tonne (for information)"})
            ex = ex.model_copy(update={"line_items": [direct, indirect]})
        docs.append(({"id": f"D{i+1}", "name": d["file"]}, ex))
    n = normalise(docs, dict(DEMO_PROFILE))
    r = calculate(n.calc_input)
    assert round(r["see"], 4) == 3.2514
    assert any("indirect" in x["note"] for x in n.notes)
