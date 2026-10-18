"""Deterministic data-quality checks that the analyst agent reasons over."""
from __future__ import annotations

import re

from .normalize import Normalized, _month_keys


def _material(desc: str) -> str:
    """'Wire rod issued' -> 'wire rod': the material, without the ledger verb."""
    d = re.sub(r"\b(issued|purchased|received|supplied|consumed|bought|delivered)\b", "", desc or "", flags=re.I)
    d = re.sub(r"\s+", " ", d).strip(" -,.")
    if not d:
        return "the precursor"
    return d[:1].lower() + d[1:] if d[1:2].islower() else d


def run_checks(norm: Normalized, profile: dict) -> list[dict]:
    ci = norm.calc_input
    findings: list[dict] = []
    period = _month_keys(profile.get("period_start"), profile.get("period_end"), None)

    def add(code, severity, title, detail, evidence=None, action=""):
        findings.append({"code": code, "severity": severity, "title": title, "detail": detail,
                         "evidence": evidence or [], "action": action})

    # Files that are not business records, and quantities outside the period
    for sa in norm.set_aside:
        add("not_a_record", "medium", f"File set aside: {sa['doc']}",
            f"This file does not look like a business record ({sa['reason']}). Nothing from it was used in the calculation.",
            action="Upload the original invoice, bill, statement or register instead.")
    # earlier months printed on a bill (an electricity bill's consumption history) are context, not a data problem
    oop = [e for e in norm.out_of_period if norm.evidence[e].kind != "electricity"]
    if oop:
        add("out_of_period", "medium", "Figures dated outside the reporting period were left out",
            f"{len(oop)} quantit{'y' if len(oop) == 1 else 'ies'} fall outside "
            f"{profile.get('period_start')} to {profile.get('period_end')} and were not counted.",
            evidence=oop, action="Check the dates, or upload records for the reporting period.")

    # Production coverage
    missing_prod = sorted(period - norm.months_with_production)
    if period and missing_prod:
        add("production_gap", "high", "Production data missing for part of the period",
            f"No production figures for {', '.join(missing_prod)}. SEE needs total output for the whole reporting period.",
            action="Upload the production register for the missing months.")

    # Fuel coverage
    missing_fuel = sorted(period - norm.fuel_months)
    if period and ci.fuels and missing_fuel:
        add("fuel_gap", "medium", "Fuel invoices do not cover every month",
            f"No fuel invoices dated in {', '.join(missing_fuel)}. If no fuel was bought that month, note it; otherwise the furnace emissions are understated.",
            action="Confirm deliveries or upload the missing invoices.")

    # Electricity coverage (low for steel: indirect not counted)
    missing_el = sorted(period - norm.electricity_months)
    if period and missing_el:
        add("electricity_gap", "low", "Electricity bills missing for some months",
            f"No electricity bill for {', '.join(missing_el)}. Not needed for the EU CBAM figure for iron & steel (direct emissions only), "
            "but keep them: buyers ask for them and other schemes (e.g. India's CCTS) use them.",
            action="Optional: upload the remaining bills.")

    # Precursor balance and yield
    for p in ci.precursors:
        bought = norm.purchases_t.get(p.cn)
        if bought:
            diff = bought - p.consumed_t
            rel = diff / bought
            sev = "info" if abs(rel) <= 0.10 else "medium"
            add("precursor_balance", sev, f"Wire rod bought vs consumed ({p.cn})",
                f"Purchased {bought:,.2f} t, consumed {p.consumed_t:,.2f} t (difference {diff:+,.2f} t, {rel:+.1%}). "
                "The difference should match the change in stock.",
                evidence=p.evidence, action="" if sev == "info" else "Check the stock register.")
        total_out = sum(g.production_t for g in ci.goods)
        if p.consumed_t > 0 and total_out > 0:
            yld = total_out / p.consumed_t
            if yld > 1.0:
                add("yield_impossible", "high", "Output exceeds wire rod input",
                    f"Fasteners produced ({total_out:,.2f} t) exceed wire rod consumed ({p.consumed_t:,.2f} t). Some input material is missing.",
                    evidence=p.evidence, action="Find the missing precursor purchases or consumption entries.")
            elif yld < 0.80:
                add("yield_low", "medium", "Unusually low material yield",
                    f"Yield {yld:.1%}. Typical cold-forging yields are well above 80%; check for unrecorded production.",
                    evidence=p.evidence)
            else:
                add("yield_ok", "info", "Material yield is plausible", f"Yield {yld:.1%} (fasteners out / wire rod in).", evidence=p.evidence)
        if p.see_actual is None:
            add("precursor_default", "high", f"Using the EU default value for {_material(p.description)}",
                f"No supplier emissions data for {p.cn}. The precursor usually makes up most of a fastener's footprint, "
                "so the default value inflates your SEE.",
                evidence=p.evidence, action="Ask the wire rod supplier for installation-level SEE data (CarbonTrail can draft the request).")
        elif not p.see_actual_verified:
            add("precursor_unverified", "medium", "Supplier data not verified",
                "The supplier's SEE is not marked as verified. From 2026 actual values used in a declaration must be verified.",
                evidence=p.see_evidence, action="Ask the supplier for the verifier's statement.")

    # Exports
    for g in ci.goods:
        if g.export_eu_t > g.production_t + 1e-9:
            add("export_gt_production", "medium", f"EU exports exceed production for {g.cn}",
                f"{g.export_eu_t:,.2f} t exported vs {g.production_t:,.2f} t produced in the period; shipments may include earlier stock.",
                evidence=g.evidence)
    for x in norm.exports_non_eu:
        dest = x["destination"]
        extra = " UK CBAM starts on 1 January 2027 and will need the same data." if dest.lower() in {"united kingdom", "uk", "great britain"} else ""
        add("non_eu_export", "info", f"Shipment to {dest} is outside EU CBAM",
            f"{x['t']:,.2f} t of {x['cn']} went to {dest}; excluded from the EU exposure.{extra}", evidence=[x["evidence"]])

    # Fuel attribution decisions worth a human look
    for f in ci.fuels:
        if f.use == "unknown":
            add("fuel_use_unknown", "medium", "Fuel use not stated",
                f"Invoice line {f.evidence} does not say what the fuel was burned in. Treated as furnace fuel (conservative).",
                evidence=f.evidence, action="Confirm whether it fired the furnace, a DG set or vehicles.")

    dg = [e for f in ci.fuels if f.use == "electricity_generation" for e in f.evidence]
    if dg:
        add("dg_excluded", "info", "DG set diesel treated as an electricity emission",
            f"Diesel burned in the generator produces electricity ({len(dg)} delivery line{'s' if len(dg) != 1 else ''}). "
            "For iron & steel goods only direct emissions count, so it is excluded from the SEE.",
            evidence=dg)

    for eid in norm.low_confidence:
        ev = norm.evidence[eid]
        fix = norm.corrections.get(eid)
        if fix:
            seen = f" ({fix})" if "→" in fix else ""
            add("low_confidence", "medium", "A handwritten figure was corrected",
                f"'{ev.description}' on {ev.doc_name} p.{ev.page} shows a correction{seen}. "
                f"CarbonTrail used {ev.quantity:g} {ev.unit or ''}".rstrip() + "; a person should confirm it.",
                evidence=[eid], action="Check this value against the original register.")
        else:
            add("low_confidence", "medium", "A number was hard to read",
                f"'{ev.description}' on {ev.doc_name} p.{ev.page} was read with low confidence ({ev.confidence:.0%}).",
                evidence=[eid], action="Check this value against the original document.")

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: order[f["severity"]])
    return findings
