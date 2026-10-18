"""Deterministic CBAM embedded-emissions engine.

The language models in CarbonTrail never do arithmetic. They read documents
and reason about them; every number shown to a user or written to an export
comes from this module, with the formula, the inputs and the evidence that
produced it.

Method (IR (EU) 2025/2547, simplified for a single production process that
makes goods of one aggregated goods category):

  Combustion emissions      Em_fuel = AD x NCV x EF x OxF
  Attributed direct         AttrEm  = sum(Em_fuel used for process heat)
                            (fuel burned to generate electricity is an
                            electricity emission: indirect, and indirect
                            emissions do not count for iron & steel goods)
  Precursor emissions       EE_InpMat = sum(M_precursor x SEE_precursor)
  Specific embedded         SEE = (AttrEm + EE_InpMat) / AL
                            where AL is the activity level (tonnes of goods
                            produced in the process during the period)

Cost exposure is shown as gross certificate exposure (tonnes exported x SEE
x certificate price). The free-allocation adjustment that reduces the
certificates actually surrendered depends on product benchmarks; it is
reported separately and never mixed into the comparison of actual vs default.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .scope import check_scope, normalise_cn

_REF_PATH = Path(__file__).parent / "data" / "reference.json"


def load_reference() -> dict:
    return json.loads(_REF_PATH.read_text())


REF = load_reference()

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

MASS_UNITS = {"t": 1.0, "tonne": 1.0, "tonnes": 1.0, "mt": 1.0, "ton": 1.0, "tons": 1.0,
              "kg": 0.001, "kgs": 0.001, "quintal": 0.1, "qtl": 0.1}
VOLUME_UNITS_KL = {"l": 0.001, "ltr": 0.001, "litre": 0.001, "litres": 0.001, "liter": 0.001,
                   "liters": 0.001, "kl": 1.0, "kilolitre": 1.0}
GAS_UNITS_KSCM = {"scm": 0.001, "sm3": 0.001, "m3": 0.001, "kscm": 1.0}
ENERGY_UNITS_GJ = {"gj": 1.0, "tj": 1000.0, "mmbtu": 1.055056}
ELEC_UNITS_MWH = {"kwh": 0.001, "mwh": 1.0, "gwh": 1000.0, "units": 0.001}


@dataclass
class FuelInput:
    id: str
    fuel: str                      # key in REF["fuels"]
    quantity: float
    unit: str
    use: str = "process_heat"      # process_heat | electricity_generation | mobile | unknown
    evidence: list[str] = field(default_factory=list)
    density_t_per_kl: float | None = None   # overrides the default density if the invoice states it


@dataclass
class PrecursorInput:
    id: str
    cn: str
    description: str
    consumed_t: float
    supplier: str = ""
    see_actual: float | None = None          # supplier's actual SEE, tCO2e/t
    see_actual_verified: bool = False
    evidence: list[str] = field(default_factory=list)
    see_evidence: list[str] = field(default_factory=list)


@dataclass
class GoodInput:
    cn: str
    description: str
    production_t: float
    export_eu_t: float = 0.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class CalcInput:
    country: str
    year: int
    period_start: str
    period_end: str
    goods: list[GoodInput]
    fuels: list[FuelInput] = field(default_factory=list)
    precursors: list[PrecursorInput] = field(default_factory=list)
    electricity_mwh: float = 0.0
    electricity_evidence: list[str] = field(default_factory=list)
    use_supplier_data: bool = True
    price_quarter: str | None = None         # e.g. "2026-Q2"; None = the quarter of period_end (2026 imports pay that quarter's price)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CalcInput":
        d = copy.deepcopy(d)
        d["goods"] = [GoodInput(**g) for g in d.get("goods", [])]
        d["fuels"] = [FuelInput(**f) for f in d.get("fuels", [])]
        d["precursors"] = [PrecursorInput(**p) for p in d.get("precursors", [])]
        return CalcInput(**d)


class CalcError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_unit(unit: str) -> str:
    return (unit or "").strip().lower().replace(".", "").replace(" ", "").strip("()[]")


# Units as printed on Indian paperwork in Hindi or Gujarati, mapped to the symbols above.
_UNIT_ALIASES = {_norm_unit(k): v for k, v in {
    "टन": "t", "टन्स": "t", "मीट्रिक टन": "mt", "मी. टन": "mt", "किलो": "kg", "किग्रा": "kg", "कि.ग्रा.": "kg",
    "किलोग्राम": "kg", "क्विंटल": "quintal", "लीटर": "l", "लिटर": "l", "ली.": "l", "किलोलीटर": "kl",
    "यूनिट": "units", "किलोवाट घंटा": "kwh",
    "ટન": "t", "મેટ્રિક ટન": "mt", "કિલો": "kg", "કિ.ગ્રા.": "kg", "કિલોગ્રામ": "kg", "લીટર": "l", "લિટર": "l",
    "યુનિટ": "units",
}.items()}


def _u(unit: str) -> str:
    u = _norm_unit(unit)
    return _UNIT_ALIASES.get(u, u)


def fuel_energy_gj(f: FuelInput, ref: dict = REF) -> tuple[float, float | None, str]:
    """Return (energy GJ, mass t or None, explanation)."""
    spec = ref["fuels"].get(f.fuel)
    if spec is None:
        raise CalcError(f"Unknown fuel '{f.fuel}'. Known: {', '.join(ref['fuels'])}")
    u = _u(f.unit)
    q = float(f.quantity)
    if u in ENERGY_UNITS_GJ:
        gj = q * ENERGY_UNITS_GJ[u]
        return gj, None, f"{q:g} {f.unit} x {ENERGY_UNITS_GJ[u]:g} GJ/{f.unit} = {gj:,.2f} GJ"
    if u in MASS_UNITS:
        t = q * MASS_UNITS[u]
        how = f"{q:g} {f.unit} = {t:,.3f} t"
    elif u in VOLUME_UNITS_KL:
        dens = f.density_t_per_kl or spec.get("density_t_per_kl")
        if not dens:
            raise CalcError(f"No density for {spec['label']} to convert {f.unit}")
        kl = q * VOLUME_UNITS_KL[u]
        t = kl * dens
        how = f"{q:g} {f.unit} = {kl:,.3f} kL x {dens:g} t/kL = {t:,.3f} t"
    elif u in GAS_UNITS_KSCM:
        dens = spec.get("density_t_per_kscm")
        if not dens:
            raise CalcError(f"Cannot convert {f.unit} for {spec['label']}")
        k = q * GAS_UNITS_KSCM[u]
        t = k * dens
        how = f"{q:g} {f.unit} x {dens:g} t per 1,000 SCM = {t:,.3f} t"
    else:
        raise CalcError(f"Unsupported unit '{f.unit}' for fuel {spec['label']}")
    gj = t * spec["ncv_gj_per_t"]
    return gj, t, f"{how}; x NCV {spec['ncv_gj_per_t']} GJ/t = {gj:,.2f} GJ"


def electricity_to_mwh(qty: float, unit: str) -> float:
    u = _u(unit)
    if u not in ELEC_UNITS_MWH:
        raise CalcError(f"Unsupported electricity unit '{unit}'")
    return float(qty) * ELEC_UNITS_MWH[u]


def to_tonnes(qty: float, unit: str) -> float:
    u = _u(unit)
    if u not in MASS_UNITS:
        raise CalcError(f"Unsupported mass unit '{unit}'")
    return float(qty) * MASS_UNITS[u]


def product_default(cn: str, country: str, ref: dict = REF) -> float | None:
    cn6 = normalise_cn(cn)[:6]
    entry = ref["product_defaults"].get(cn6)
    if not entry:
        return None
    return entry["values"].get(country)


def precursor_default(cn: str, country: str, ref: dict = REF) -> float | None:
    cn4 = normalise_cn(cn)[:4]
    entry = ref["precursor_defaults"].get(cn4)
    if not entry:
        return None
    return entry["values"].get(country)


def markup_for_year(year: int, ref: dict = REF) -> float:
    m = ref["default_markup"]
    if year <= 2026:
        return m["2026"]
    if year == 2027:
        return m["2027"]
    return m["2028"]


def quarter_of(date: str) -> str | None:
    """'2026-06-30' -> '2026-Q2'."""
    try:
        y, m = int(date[:4]), int(date[5:7])
    except (TypeError, ValueError):
        return None
    return f"{y}-Q{(m - 1) // 3 + 1}"


def certificate_price(quarter: str | None, ref: dict = REF, period_end: str | None = None) -> tuple[float, str]:
    """2026 imports pay the price of the quarter they are imported in (Art. 21(1a)).
    Without an explicit quarter, use the quarter the reporting period ends in; if that
    price is not published yet, fall back to the latest published one."""
    prices = ref["certificate_price_eur"]
    q = quarter or quarter_of(period_end or "") or prices["latest"]
    if q not in prices:
        q = prices["latest"]
    return float(prices[q]), q


def free_allocation_benchmark(cn: str, ref: dict = REF) -> float | None:
    fab = ref.get("free_allocation_benchmark", {})
    c = normalise_cn(cn).replace(" ", "")
    for k, v in fab.items():
        if not k.startswith("_") and c.startswith(k):
            return float(v)
    return None


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def calculate(inp: CalcInput, ref: dict = REF) -> dict[str, Any]:
    if not inp.goods:
        raise CalcError("At least one good with a production quantity is needed.")
    steps: list[dict] = []
    warnings: list[str] = []

    # 1. Scope
    scopes = {g.cn: check_scope(g.cn) for g in inp.goods}
    out_of_scope = [cn for cn, s in scopes.items() if not s.in_scope]
    if out_of_scope:
        warnings.append(f"Not CBAM goods and excluded from the calculation: {', '.join(out_of_scope)}.")
    goods = [g for g in inp.goods if scopes[g.cn].in_scope]
    if not goods:
        raise CalcError("None of the goods are CBAM goods.")
    cats = {scopes[g.cn].aggregated_category for g in goods}
    if len(cats) > 1:
        warnings.append("Goods span several aggregated goods categories; CarbonTrail treats them as one production process. Split processes before filing.")
    direct_only = all(scopes[g.cn].sector in {"iron_steel", "aluminium", "hydrogen"} for g in goods)

    # 2. Fuel combustion
    fuel_rows = []
    attributed = 0.0
    excluded = 0.0
    for f in inp.fuels:
        spec = ref["fuels"][f.fuel]
        gj, mass_t, how = fuel_energy_gj(f, ref)
        co2 = gj / 1000.0 * spec["ef_t_per_tj"] * 1.0
        counted = f.use == "process_heat" or f.use == "unknown"
        reason = {
            "process_heat": "Burned for process heat inside the installation: direct emissions, attributed to the production process.",
            "unknown": "Use not stated on the document: treated as process heat (conservative). Confirm with the plant.",
            "electricity_generation": "Burned to generate electricity (DG set): an electricity emission, i.e. indirect. Not counted for iron & steel goods.",
            "mobile": "Mobile combustion (vehicles, forklifts) outside the production process boundary: not attributed.",
        }[f.use]
        if f.use == "electricity_generation" and not direct_only:
            counted = True
            reason = "Electricity generated on site; counted because indirect emissions apply to this sector."
        if counted:
            attributed += co2
        else:
            excluded += co2
        fuel_rows.append({
            "id": f.id, "fuel": f.fuel, "label": spec["label"], "quantity": f.quantity, "unit": f.unit,
            "mass_t": mass_t, "energy_gj": gj, "ef_t_per_tj": spec["ef_t_per_tj"], "co2_t": co2,
            "counted": counted, "use": f.use, "reason": reason, "evidence": f.evidence,
        })
        steps.append({
            "id": f"fuel:{f.id}",
            "title": f"Combustion emissions: {spec['label']}",
            "formula": "Em = AD x NCV x EF x OxF",
            "working": f"{how}; x EF {spec['ef_t_per_tj']} tCO2/TJ x OxF 1.0 = {co2:,.3f} tCO2",
            "result": co2, "unit": "tCO2",
            "counted": counted, "note": reason, "evidence": f.evidence,
            "rule": "IR (EU) 2025/2547 standard method; IPCC 2006 default NCV/EF",
        })
    if inp.fuels and attributed == 0:
        warnings.append("No fuel was attributed to process heat. Check that furnace fuels are included.")
    if not inp.fuels:
        warnings.append("No fuel documents found. Direct process emissions are assumed to be zero, which is unusual for a heat-treatment plant.")
    steps.append({
        "id": "attr", "title": "Directly attributed emissions of the production process",
        "formula": "AttrEm = sum of counted combustion emissions",
        "working": " + ".join(f"{r['co2_t']:,.3f}" for r in fuel_rows if r["counted"]) + f" = {attributed:,.3f} tCO2" if fuel_rows else "0",
        "result": attributed, "unit": "tCO2", "evidence": [e for r in fuel_rows if r["counted"] for e in r["evidence"]],
        "rule": "IR (EU) 2025/2547 attribution to production processes",
    })

    # 3. Precursors
    prec_rows = []
    ee_inp = 0.0
    for p in inp.precursors:
        default = precursor_default(p.cn, inp.country, ref)
        use_actual = inp.use_supplier_data and p.see_actual is not None
        if use_actual:
            see, basis, ev = float(p.see_actual), "Supplier actual data" + (" (verified)" if p.see_actual_verified else " (not yet verified)"), p.see_evidence
            if not p.see_actual_verified:
                warnings.append(f"Supplier SEE for {p.description} is not marked as verified. From 2026 actual values must be verified by an accredited verifier.")
        elif default is not None:
            see, basis, ev = default, f"EU default value ({inp.country})", []
        else:
            raise CalcError(f"No SEE for precursor {p.description} ({p.cn}): request supplier data.")
        ee = p.consumed_t * see
        ee_inp += ee
        prec_rows.append({
            "id": p.id, "cn": p.cn, "description": p.description, "supplier": p.supplier,
            "consumed_t": p.consumed_t, "see": see, "basis": basis, "default_see": default,
            "ee_t": ee, "evidence": p.evidence, "see_evidence": ev,
        })
        steps.append({
            "id": f"prec:{p.id}", "title": f"Embedded emissions of precursor: {p.description}",
            "formula": "EE = M_precursor x SEE_precursor",
            "working": f"{p.consumed_t:,.3f} t x {see:,.3f} tCO2e/t ({basis}) = {ee:,.3f} tCO2e",
            "result": ee, "unit": "tCO2e", "evidence": p.evidence + ev,
            "rule": "IR (EU) 2025/2547: embedded emissions of relevant precursors consumed in the process",
        })

    # 4. Activity level and SEE
    al = sum(g.production_t for g in goods)
    if al <= 0:
        raise CalcError("Total production must be greater than zero.")
    total = attributed + ee_inp
    see = total / al
    steps.append({
        "id": "see", "title": "Specific embedded emissions (SEE) of the goods",
        "formula": "SEE = (AttrEm + EE_InpMat) / AL",
        "working": f"({attributed:,.3f} + {ee_inp:,.3f}) / {al:,.3f} t = {see:,.4f} tCO2e/t",
        "result": see, "unit": "tCO2e/t", "evidence": [e for g in goods for e in g.evidence],
        "rule": "IR (EU) 2025/2547: SEE of goods of one aggregated goods category",
    })

    # 5. Compare with defaults and price exposure
    markup = markup_for_year(inp.year, ref)
    price, quarter = certificate_price(inp.price_quarter, ref, inp.period_end)
    cbam_factor = float(ref.get("cbam_factor", {}).get(str(inp.year), ref.get("cbam_factor", {}).get("2026", 0.975)))
    good_rows = []
    tot_exp = tot_actual_cost = tot_default_cost = 0.0
    tot_fa = tot_net_actual = tot_net_default = 0.0
    fa_complete = True
    for g in goods:
        base = product_default(g.cn, inp.country, ref)
        marked = base * (1 + markup) if base is not None else None
        emb_actual = g.export_eu_t * see
        cost_actual = emb_actual * price
        cost_default = g.export_eu_t * marked * price if marked is not None else None
        saving = (cost_default - cost_actual) if cost_default is not None else None
        # Free-allocation adjustment (estimate): certificates are reduced by CBAM factor x benchmark per tonne.
        bm = free_allocation_benchmark(g.cn, ref)
        fa_t = g.export_eu_t * cbam_factor * bm if bm is not None else None
        net_actual = max(0.0, emb_actual - fa_t) * price if fa_t is not None else None
        net_default = max(0.0, g.export_eu_t * marked - fa_t) * price if (fa_t is not None and marked is not None) else None
        if fa_t is None or net_default is None:
            fa_complete = False
        else:
            tot_fa += fa_t
            tot_net_actual += net_actual
            tot_net_default += net_default
        tot_exp += g.export_eu_t
        tot_actual_cost += cost_actual
        if cost_default is not None:
            tot_default_cost += cost_default
        else:
            warnings.append(f"No EU default value loaded for {g.cn} / {inp.country}; comparison skipped for this good.")
        good_rows.append({
            "cn": normalise_cn(g.cn), "description": g.description, "production_t": g.production_t,
            "export_eu_t": g.export_eu_t, "see": see, "default_base": base, "markup": markup,
            "default_marked": marked, "embedded_in_exports_t": emb_actual,
            "gross_cost_actual_eur": cost_actual, "gross_cost_default_eur": cost_default,
            "saving_eur": saving, "free_allocation_benchmark": bm, "free_allocation_t": fa_t,
            "net_cost_actual_eur": net_actual, "net_cost_default_eur": net_default, "evidence": g.evidence,
            "aggregated_category": scopes[g.cn].aggregated_category,
        })
        if g.export_eu_t > g.production_t + 1e-9:
            warnings.append(f"EU exports of {g.cn} ({g.export_eu_t:g} t) exceed production in the period ({g.production_t:g} t). Check stock or periods.")
    steps.append({
        "id": "cost", "title": "Gross certificate exposure on EU exports",
        "formula": "Exposure = tonnes exported x SEE x certificate price",
        "working": f"{tot_exp:,.3f} t exported; certificate price EUR {price:.2f}/tCO2e ({quarter}); defaults carry a {markup:.0%} mark-up in {inp.year}",
        "result": tot_actual_cost, "unit": "EUR", "evidence": [],
        "rule": "Art. 21(1a) Reg. 2023/956 quarterly price for 2026 (the price of the quarter of import); IR (EU) 2025/2621 mark-up",
    })
    if fa_complete and tot_exp:
        steps.append({
            "id": "free_allocation", "title": "Free-allocation adjustment (estimate)",
            "formula": "Certificates to surrender = embedded emissions - (CBAM factor x benchmark x tonnes)",
            "working": (f"{cbam_factor:.1%} x default benchmark {tot_fa / cbam_factor / tot_exp:,.3f} tCO2e/t x {tot_exp:,.3f} t = {tot_fa:,.3f} tCO2e deducted; "
                        f"net EUR {tot_net_actual:,.0f} with your data vs EUR {tot_net_default:,.0f} on defaults"),
            "result": tot_net_actual, "unit": "EUR", "evidence": [],
            "rule": "Art. 31 Reg. 2023/956; IR (EU) 2025/2620 benchmarks (column B, applied with default values). With verified actual data the operator computes the adjustment from its own process, so this is an estimate.",
        })

    share_prec = ee_inp / total if total else 0.0
    return {
        "inputs": inp.to_dict(),
        "scope": {cn: s.to_dict() for cn, s in scopes.items()},
        "direct_only": direct_only,
        "fuels": fuel_rows,
        "precursors": prec_rows,
        "attributed_direct_t": attributed,
        "excluded_t": excluded,
        "precursor_emissions_t": ee_inp,
        "total_embedded_t": total,
        "activity_level_t": al,
        "see": see,
        "breakdown": {"process_fuels": attributed / total if total else 0.0, "precursors": share_prec},
        "electricity_mwh": inp.electricity_mwh,
        "electricity_note": "Electricity is recorded for completeness. Indirect emissions are not counted in the CBAM obligation for iron & steel goods (Annex II)." if direct_only else "",
        "goods": good_rows,
        "totals": {
            "export_eu_t": tot_exp,
            "gross_cost_actual_eur": tot_actual_cost,
            "gross_cost_default_eur": tot_default_cost,
            "saving_eur": tot_default_cost - tot_actual_cost if tot_default_cost else None,
            "free_allocation_t": tot_fa if fa_complete else None,
            "net_cost_actual_eur": tot_net_actual if fa_complete else None,
            "net_cost_default_eur": tot_net_default if fa_complete else None,
        },
        "cbam_factor": cbam_factor,
        "price": {"eur_per_t": price, "quarter": quarter},
        "markup": markup,
        "steps": steps,
        "warnings": warnings,
        "free_allocation_note": (
            (f"After the free-allocation adjustment (CBAM factor {cbam_factor:.1%} x benchmark) an estimated EUR {tot_net_actual:,.0f} "
             f"is payable with your data and EUR {tot_net_default:,.0f} on defaults. " if fa_complete else
             f"Certificates actually surrendered are lower after the free-allocation adjustment (CBAM factor {cbam_factor:.1%} x benchmark). ")
            + "The adjustment is applied to both, so the gap between actual data and default values is what better data saves."
        ),
    }


# ---------------------------------------------------------------------------
# What-if scenarios
# ---------------------------------------------------------------------------

def run_scenario(inp: CalcInput, params: dict, ref: dict = REF) -> dict[str, Any]:
    """Apply what-if changes to a copy of the inputs and compare with the base case."""
    base = calculate(inp, ref)
    s = CalcInput.from_dict(inp.to_dict())
    applied = []
    if params.get("precursor_see") is not None:
        for p in s.precursors:
            p.see_actual = float(params["precursor_see"])
            p.see_actual_verified = True
        s.use_supplier_data = True
        applied.append(f"Precursor sourced from a supplier with SEE {float(params['precursor_see']):.2f} tCO2e/t")
    if params.get("fuel_switch_to"):
        target = params["fuel_switch_to"]
        if target not in ref["fuels"]:
            raise CalcError(f"Unknown fuel {target}")
        new_fuels = []
        for f in s.fuels:
            if f.use in ("process_heat", "unknown") and f.fuel != target:
                gj, _, _ = fuel_energy_gj(f, ref)
                new_fuels.append(FuelInput(id=f.id + "-switched", fuel=target, quantity=gj, unit="GJ", use=f.use, evidence=f.evidence))
            else:
                new_fuels.append(f)
        s.fuels = new_fuels
        applied.append(f"Furnace fuel switched to {ref['fuels'][target]['label']} (same heat delivered)")
    if params.get("yield_gain_pct"):
        pct = float(params["yield_gain_pct"]) / 100.0
        for p in s.precursors:
            p.consumed_t = p.consumed_t * (1 - pct)
        applied.append(f"Material yield improved: {pct:.0%} less wire rod per tonne of fasteners")
    if params.get("renewable_electricity_pct"):
        applied.append(f"{params['renewable_electricity_pct']}% renewable electricity: no change to the CBAM figure for iron & steel (indirect emissions are not counted)")
    if params.get("year"):
        s.year = int(params["year"])
        applied.append(f"Default mark-up for {s.year}: {markup_for_year(s.year, ref):.0%}")
    out = calculate(s, ref)
    return {
        "applied": applied,
        "base": {"see": base["see"], "cost": base["totals"]["gross_cost_actual_eur"], "default_cost": base["totals"]["gross_cost_default_eur"]},
        "scenario": {"see": out["see"], "cost": out["totals"]["gross_cost_actual_eur"], "default_cost": out["totals"]["gross_cost_default_eur"]},
        "delta_see": out["see"] - base["see"],
        "delta_cost_eur": out["totals"]["gross_cost_actual_eur"] - base["totals"]["gross_cost_actual_eur"],
        "result": out,
    }


def country_comparison(cn: str, ref: dict = REF) -> list[dict]:
    entry = ref["product_defaults"].get(normalise_cn(cn)[:6])
    if not entry:
        return []
    m = markup_for_year(2026, ref)
    return sorted(
        [{"country": c, "default": v, "default_2026": v * (1 + m)} for c, v in entry["values"].items()],
        key=lambda r: r["default"],
    )
