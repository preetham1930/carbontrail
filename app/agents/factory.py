"""Google ADK agents for CarbonTrail.

Agent map
---------
document_intake_agent   Gemini multimodal; one run per document (run in parallel); structured output.
scope_quality_agent  -+
calculation_agent     +-- SequentialAgent "cbam_analysis_pipeline"; each calls deterministic tools.
advisor_agent        -+
supplier_data_agent     Drafts the precursor-data request to the wire-rod supplier.
regulation_agent        Answers CBAM questions from curated, cited notes.

All numbers come from deterministic tools (app.cbam.engine); the agents read,
decide, explain and act.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from google.adk.agents import LlmAgent, SequentialAgent
from google.genai import types

from .. import config
from ..cbam.engine import calculate, run_scenario, CalcError
from ..cbam.scope import check_scope
from ..schemas import DocumentExtraction
from . import prompts

MODEL_OVERRIDE = None  # tests inject a scripted model here

_KNOWLEDGE = (Path(__file__).resolve().parent.parent / "cbam" / "data" / "knowledge.md").read_text()


def _cfg(temperature: float = 0.2, json_mode: bool = False, thinking: str | None = None) -> types.GenerateContentConfig:
    kw: dict[str, Any] = {}
    level = (config.THINKING_LEVEL if thinking is None else thinking).upper()
    if level in ("MINIMAL", "LOW", "MEDIUM", "HIGH"):
        # Gemini 3 models: control latency with the thinking level and keep the default temperature (1.0),
        # which Google recommends for Gemini 3; lower values can cause loops in tool use.
        kw["thinking_config"] = types.ThinkingConfig(thinking_level=getattr(types.ThinkingLevel, level))
    else:
        kw["temperature"] = temperature
    if json_mode:
        kw["response_mime_type"] = "application/json"
    return types.GenerateContentConfig(**kw)


def _static(text: str) -> Callable:
    """Instruction provider: returns the text verbatim (no {state} templating)."""
    def provider(_ctx) -> str:
        return text
    return provider


def build_intake_agent(model=None) -> LlmAgent:
    return LlmAgent(
        name="document_intake_agent",
        description="Reads one factory document (scan/photo/PDF) and extracts CBAM activity data with source quotes.",
        model=model or MODEL_OVERRIDE or config.MODEL_INTAKE,
        instruction=_static(prompts.INTAKE),
        output_schema=DocumentExtraction,
        output_key="extraction",
        generate_content_config=_cfg(0.0, thinking=config.THINKING_LEVEL_INTAKE),
    )


def _r(x: Optional[float], n: int = 3) -> Optional[float]:
    return None if x is None else round(float(x), n)


def build_analysis_pipeline(ctx: "AnalysisContext", model=None) -> SequentialAgent:
    """Three agents in sequence, sharing tools bound to this run's context."""
    m = model or MODEL_OVERRIDE or config.MODEL_REASON

    def get_activity_data() -> dict:
        """Return the activity data extracted from the factory's documents: goods produced and exported, fuels, precursors, electricity, and normalisation notes."""
        ci = ctx.calc_input
        return {
            "country": ci.country, "year": ci.year, "period": [ci.period_start, ci.period_end],
            "goods": [{"cn": g.cn, "description": g.description, "production_t": _r(g.production_t), "export_eu_t": _r(g.export_eu_t), "evidence": g.evidence} for g in ci.goods],
            "fuels": [{"fuel": f.fuel, "quantity": f.quantity, "unit": f.unit, "use": f.use, "evidence": f.evidence} for f in ci.fuels],
            "precursors": [{"cn": p.cn, "description": p.description, "consumed_t": _r(p.consumed_t), "supplier": p.supplier,
                            "supplier_see": p.see_actual, "supplier_see_verified": p.see_actual_verified} for p in ci.precursors],
            "electricity_mwh": _r(ci.electricity_mwh),
            "non_eu_exports": ctx.normalized.exports_non_eu,
            "normalisation_notes": [n["note"] for n in ctx.normalized.notes],
        }

    def check_cbam_scope(cn_codes: list[str]) -> dict:
        """Check whether CN codes are covered by EU CBAM, their sector, aggregated goods category, and whether only direct emissions count."""
        return {cn: check_scope(cn).to_dict() for cn in cn_codes}

    def run_data_quality_checks() -> dict:
        """Run deterministic reconciliation checks (period coverage, precursor balance, yield, exports vs production, fuel attribution, low-confidence readings)."""
        return {"findings": ctx.findings}

    def record_finding(severity: str, title: str, explanation: str, recommended_action: str) -> dict:
        """Record a finding for the owner and the verifier. severity is one of: high, medium, low."""
        ctx.agent_findings.append({"severity": severity if severity in ("high", "medium", "low") else "medium",
                                   "title": title, "detail": explanation, "action": recommended_action})
        return {"recorded": True, "count": len(ctx.agent_findings)}

    def calculate_embedded_emissions() -> dict:
        """Calculate specific embedded emissions (SEE) with the EU CBAM method. Returns SEE, totals, the share from precursors and fuels, and excluded sources."""
        try:
            r = ctx.ensure_result()
        except CalcError as e:
            return {"error": str(e)}
        return {
            "see_tco2e_per_t": _r(r["see"], 4),
            "activity_level_t": _r(r["activity_level_t"]),
            "attributed_direct_emissions_t": _r(r["attributed_direct_t"]),
            "precursor_emissions_t": _r(r["precursor_emissions_t"]),
            "excluded_emissions_t": _r(r["excluded_t"]),
            "share_from_precursors": _r(r["breakdown"]["precursors"], 3),
            "share_from_fuels": _r(r["breakdown"]["process_fuels"], 3),
            "precursor_basis": [{"description": p["description"], "see_used": p["see"], "basis": p["basis"]} for p in r["precursors"]],
            "excluded_sources": [{"fuel": f["label"], "co2_t": _r(f["co2_t"]), "reason": f["reason"]} for f in r["fuels"] if not f["counted"]],
            "warnings": r["warnings"],
        }

    def compare_with_eu_defaults() -> dict:
        """Compare the factory's actual SEE with the EU default values (with this year's mark-up) and give gross certificate exposure in euros for EU exports."""
        try:
            r = ctx.ensure_result()
        except CalcError as e:
            return {"error": str(e)}
        return {
            "certificate_price_eur": r["price"]["eur_per_t"], "price_quarter": r["price"]["quarter"],
            "default_markup": r["markup"],
            "goods": [{"cn": g["cn"], "description": g["description"], "export_eu_t": _r(g["export_eu_t"]),
                       "actual_see": _r(g["see"], 4), "eu_default_with_markup": _r(g["default_marked"], 4),
                       "gross_cost_actual_eur": _r(g["gross_cost_actual_eur"], 0), "gross_cost_default_eur": _r(g["gross_cost_default_eur"], 0)} for g in r["goods"]],
            "total_gross_cost_actual_eur": _r(r["totals"]["gross_cost_actual_eur"], 0),
            "total_gross_cost_default_eur": _r(r["totals"]["gross_cost_default_eur"], 0),
            "saving_vs_default_eur": _r(r["totals"]["saving_eur"], 0),
            "note": r["free_allocation_note"],
        }

    def simulate_scenario(precursor_see: Optional[float] = None, fuel_switch_to: Optional[str] = None,
                          yield_gain_pct: Optional[float] = None, renewable_electricity_pct: Optional[float] = None) -> dict:
        """What-if analysis. precursor_see: SEE of an alternative precursor supplier (tCO2e/t). fuel_switch_to: one of natural_gas, lpg, furnace_oil, coal. yield_gain_pct: % less precursor per tonne of output. renewable_electricity_pct: share of renewable power."""
        params = {k: v for k, v in {"precursor_see": precursor_see, "fuel_switch_to": fuel_switch_to,
                                    "yield_gain_pct": yield_gain_pct, "renewable_electricity_pct": renewable_electricity_pct}.items() if v not in (None, "", 0)}
        try:
            s = run_scenario(ctx.calc_input, params)
        except CalcError as e:
            return {"error": str(e)}
        out = {"applied": s["applied"], "see_before": _r(s["base"]["see"], 4), "see_after": _r(s["scenario"]["see"], 4),
               "gross_cost_change_eur_this_period": _r(s["delta_cost_eur"], 0)}
        ctx.agent_scenarios.append({"params": params, **out})
        return out

    quality = LlmAgent(
        name="scope_quality_agent", model=m,
        description="Checks CBAM scope and whether the data is fit for verification; records findings.",
        instruction=_static(prompts.QUALITY),
        tools=[get_activity_data, check_cbam_scope, run_data_quality_checks, record_finding],
        output_key="quality_review", generate_content_config=_cfg(0.2),
    )
    calc = LlmAgent(
        name="calculation_agent", model=m,
        description="Runs the EU CBAM calculation via deterministic tools and explains it like a verifier.",
        instruction=_static(prompts.CALC),
        tools=[calculate_embedded_emissions, compare_with_eu_defaults],
        output_key="calc_review", generate_content_config=_cfg(0.1),
    )
    advisor = LlmAgent(
        name="advisor_agent", model=m,
        description="Turns the result into euros and ranked levers for the owner.",
        instruction=_static(prompts.ADVISOR),
        tools=[compare_with_eu_defaults, simulate_scenario],
        output_key="advice", generate_content_config=_cfg(0.3),
    )
    return SequentialAgent(name="cbam_analysis_pipeline", sub_agents=[quality, calc, advisor],
                           description="Quality review -> calculation -> advice")


def build_supplier_agent(model=None) -> LlmAgent:
    return LlmAgent(
        name="supplier_data_agent", model=model or MODEL_OVERRIDE or config.MODEL_REASON,
        description="Drafts the request for installation-level emissions data to a precursor supplier.",
        instruction=_static(prompts.SUPPLIER), generate_content_config=_cfg(0.4, json_mode=True),
    )


def build_regulation_agent(model=None) -> LlmAgent:
    return LlmAgent(
        name="regulation_agent", model=model or MODEL_OVERRIDE or config.MODEL_REASON,
        description="Answers CBAM questions from curated, cited regulation notes.",
        instruction=_static(prompts.REGULATION.replace("{knowledge}", _KNOWLEDGE)),
        generate_content_config=_cfg(0.2),
    )


class AnalysisContext:
    """Mutable per-run state shared by the analysis tools."""

    def __init__(self, normalized, findings: list[dict]):
        self.normalized = normalized
        self.calc_input = normalized.calc_input
        self.findings = findings
        self.agent_findings: list[dict] = []
        self.agent_scenarios: list[dict] = []
        self.result: dict | None = None

    def ensure_result(self) -> dict:
        if self.result is None:
            self.result = calculate(self.calc_input)
        return self.result
