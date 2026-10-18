"""Turn document extractions into engine inputs, keeping an evidence trail.

Every quantity that reaches the engine keeps the IDs of the document lines it
came from, so each number in the final report can be traced back to the exact
words on the exact page of the source document.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .cbam.engine import (CalcInput, FuelInput, GoodInput, PrecursorInput, CalcError,
                          MASS_UNITS, electricity_to_mwh, to_tonnes, REF)
from .cbam.scope import check_scope, normalise_cn
from .schemas import DocumentExtraction, LineItem

EU_COUNTRIES = {
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "czech republic", "denmark", "estonia",
    "finland", "france", "germany", "greece", "hungary", "ireland", "italy", "latvia", "lithuania", "luxembourg",
    "malta", "netherlands", "the netherlands", "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
}

_TOTAL_RE = re.compile(r"\b(total|grand total|sub ?total|kul|योग|कुल)\b", re.I)


@dataclass
class Evidence:
    id: str
    doc_id: str
    doc_name: str
    doc_type: str
    page: int
    quote: str
    kind: str
    description: str
    quantity: float | None
    unit: str | None
    confidence: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Normalized:
    calc_input: CalcInput
    evidence: dict[str, Evidence]
    notes: list[dict] = field(default_factory=list)          # normalisation decisions, for the trace
    purchases_t: dict[str, float] = field(default_factory=dict)
    exports_non_eu: list[dict] = field(default_factory=list)
    months_with_production: set[str] = field(default_factory=set)
    electricity_months: set[str] = field(default_factory=set)
    fuel_months: set[str] = field(default_factory=set)
    low_confidence: list[str] = field(default_factory=list)
    supplier_names: list[str] = field(default_factory=list)
    set_aside: list[dict] = field(default_factory=list)       # files that are not business records
    out_of_period: list[str] = field(default_factory=list)    # evidence ids dated outside the reporting period


def _month_keys(start: str | None, end: str | None, fallback: str | None) -> set[str]:
    def p(s):
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None
    a, b = p(start or ""), p(end or "")
    if a and b and a <= b:
        keys, y, m = set(), a.year, a.month
        while (y, m) <= (b.year, b.month):
            keys.add(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return keys
    for s in (start, end, fallback):
        d = p(s or "")
        if d:
            return {f"{d.year:04d}-{d.month:02d}"}
    return set()


def _fuel_key(item: LineItem) -> str | None:
    if item.fuel_type and item.fuel_type != "other":
        return item.fuel_type
    d = (item.description or "").lower()
    for key, words in {
        "lpg": ["lpg", "propane", "butane"], "natural_gas": ["png", "natural gas", "cng"],
        "diesel": ["diesel", "hsd"], "furnace_oil": ["furnace oil", "fo ", "lshs"],
        "petrol": ["petrol", "gasoline", "ms "], "coal": ["coal"],
    }.items():
        if any(w in d for w in words):
            return key
    return None


def _fuel_use(item: LineItem) -> str:
    if item.fuel_use:
        return item.fuel_use
    d = (item.description or "").lower()
    if any(w in d for w in ["dg set", "genset", "generator", "d.g."]):
        return "electricity_generation"
    if any(w in d for w in ["furnace", "heat treat", "annealing", "hardening", "boiler"]):
        return "process_heat"
    if any(w in d for w in ["vehicle", "forklift", "truck"]):
        return "mobile"
    return "unknown"


def _drop_total_rows(items: list[LineItem], kind: str) -> tuple[list[LineItem], list[str]]:
    """If a document lists monthly rows plus a 'Total' row, keep only the rows."""
    rows = [i for i in items if i.kind == kind]
    totals = [i for i in rows if _TOTAL_RE.search(i.description or "")]
    others = [i for i in rows if i not in totals]
    dropped = []
    if totals and others:
        keep = [i for i in items if i not in totals]
        dropped = [t.description for t in totals]
        return keep, dropped
    return items, dropped


_PERIOD_KINDS = {"fuel", "electricity", "precursor_purchase", "precursor_consumption", "production", "export", "scrap"}


def _date(s: str | None):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _outside(it: LineItem, ex: DocumentExtraction, p0, p1) -> bool:
    """True when a quantity is dated wholly outside the reporting period. A dated period must overlap it;
    a document date alone may fall up to 45 days after the period (bills are issued after the month)."""
    if not (p0 and p1):
        return False
    s, e = _date(it.period_start or ex.period_start), _date(it.period_end or ex.period_end)
    if s or e:
        return bool((e and e < p0) or (s and s > p1))
    d = _date(ex.document_date)
    return bool(d and (d < p0 or (d - p1).days > 45))


def normalise(docs: list[tuple[dict, DocumentExtraction]], profile: dict) -> Normalized:
    country = profile.get("country", "India")
    evidence: dict[str, Evidence] = {}
    notes: list[dict] = []
    fuels: list[FuelInput] = []
    elec_mwh = 0.0
    elec_ev: list[str] = []
    production: dict[str, dict] = defaultdict(lambda: {"t": 0.0, "desc": "", "ev": []})
    exports_eu: dict[str, dict] = defaultdict(lambda: {"t": 0.0, "ev": []})
    exports_non_eu: list[dict] = []
    consumption: dict[str, dict] = defaultdict(lambda: {"t": 0.0, "desc": "", "ev": []})
    purchases: dict[str, dict] = defaultdict(lambda: {"t": 0.0, "desc": "", "ev": [], "supplier": ""})
    supplier_see: list[tuple[LineItem, str]] = []
    out = Normalized(calc_input=None, evidence=evidence)  # type: ignore[arg-type]
    n = 0

    p0 = _date(profile.get("period_start")); p1 = _date(profile.get("period_end"))
    for meta, ex in docs:
        if not getattr(ex, "is_source_record", True):
            reason = (ex.not_a_record_reason or "it does not look like an invoice, bill, statement or register").strip().rstrip(".")
            out.set_aside.append({"doc": meta["name"], "reason": reason})
            notes.append({"doc": meta["name"], "note": f"Set aside {meta['name']}: {reason}. No figures were taken from it."})
            continue
        items = list(ex.line_items)
        for kind in ("production", "precursor_consumption", "fuel", "export", "precursor_purchase", "electricity"):
            items, dropped = _drop_total_rows(items, kind)
            if dropped:
                notes.append({"doc": meta["name"], "note": f"Ignored total row(s) {dropped} to avoid double counting; the individual rows are used."})
        for it in items:
            n += 1
            eid = f"E{n}"
            evidence[eid] = Evidence(eid, meta["id"], meta["name"], ex.doc_type, it.page or 1, (it.source_quote or "")[:200],
                                     it.kind, it.description, it.quantity, it.unit, float(it.confidence or 0))
            if (it.confidence if it.confidence is not None else 1.0) < 0.75:
                out.low_confidence.append(eid)
            if it.quantity is None:
                continue
            if it.kind in _PERIOD_KINDS and _outside(it, ex, p0, p1):
                out.out_of_period.append(eid)
                notes.append({"doc": meta["name"], "note": f"Left out '{it.description}' ({it.quantity:g} {it.unit or ''}): dated outside the reporting period."})
                continue
            months = _month_keys(it.period_start or ex.period_start, it.period_end or ex.period_end, ex.document_date)
            try:
                if it.kind == "fuel":
                    key = _fuel_key(it)
                    if key is None:
                        notes.append({"doc": meta["name"], "note": f"Could not identify fuel '{it.description}'; left out. Please confirm the fuel type."})
                        continue
                    use = _fuel_use(it)
                    fuels.append(FuelInput(id=eid, fuel=key, quantity=float(it.quantity), unit=it.unit or "", use=use, evidence=[eid]))
                    out.fuel_months |= months
                elif it.kind == "electricity":
                    elec_mwh += electricity_to_mwh(it.quantity, it.unit or "kWh")
                    elec_ev.append(eid)
                    out.electricity_months |= months
                elif it.kind == "production":
                    cn = normalise_cn(it.cn_code or "")
                    if not cn:
                        notes.append({"doc": meta["name"], "note": f"Production line '{it.description}' has no CN code; please map it."})
                        continue
                    production[cn]["t"] += to_tonnes(it.quantity, it.unit or "t")
                    production[cn]["desc"] = production[cn]["desc"] or it.description
                    production[cn]["ev"].append(eid)
                    out.months_with_production |= months
                elif it.kind == "precursor_consumption":
                    cn = normalise_cn(it.cn_code or "7213")[:4]
                    consumption[cn]["t"] += to_tonnes(it.quantity, it.unit or "t")
                    consumption[cn]["desc"] = consumption[cn]["desc"] or it.description
                    consumption[cn]["ev"].append(eid)
                elif it.kind == "precursor_purchase":
                    cn = normalise_cn(it.cn_code or "7213")[:4]
                    purchases[cn]["t"] += to_tonnes(it.quantity, it.unit or "t")
                    purchases[cn]["desc"] = purchases[cn]["desc"] or it.description
                    purchases[cn]["ev"].append(eid)
                    purchases[cn]["supplier"] = purchases[cn]["supplier"] or (it.party or ex.issuer or "")
                elif it.kind == "export":
                    cn = normalise_cn(it.cn_code or "")
                    dest = (it.destination_country or "").strip()
                    t = to_tonnes(it.quantity, it.unit or "t")
                    if dest.lower() in EU_COUNTRIES:
                        exports_eu[cn]["t"] += t
                        exports_eu[cn]["ev"].append(eid)
                    else:
                        exports_non_eu.append({"cn": cn, "t": t, "destination": dest or "unknown", "evidence": eid})
                elif it.kind == "supplier_see":
                    supplier_see.append((it, eid))
                    if it.party:
                        out.supplier_names.append(it.party)
            except CalcError as e:
                notes.append({"doc": meta["name"], "note": f"Skipped '{it.description}': {e}"})

    # Goods
    goods = []
    for cn, p in production.items():
        if not check_scope(cn).in_scope:
            notes.append({"doc": "-", "note": f"{cn} is not a CBAM good; excluded."})
            continue
        exp = exports_eu.get(cn, {"t": 0.0, "ev": []})
        goods.append(GoodInput(cn=cn, description=_clean_desc(p["desc"]), production_t=round(p["t"], 6), export_eu_t=round(exp["t"], 6), evidence=p["ev"] + exp["ev"]))
    for cn in exports_eu:
        if cn not in production:
            notes.append({"doc": "-", "note": f"EU exports of {cn} found but no production of {cn} in the period."})

    # Precursors: prefer consumption from the production register; fall back to purchases
    precursors = []
    for cn in set(consumption) | set(purchases):
        if cn in consumption and consumption[cn]["t"] > 0:
            t, desc, ev = consumption[cn]["t"], consumption[cn]["desc"], consumption[cn]["ev"]
        else:
            t, desc, ev = purchases[cn]["t"], purchases[cn]["desc"], purchases[cn]["ev"]
            notes.append({"doc": "-", "note": f"No consumption figure for precursor {cn}; using purchases ({t:.3f} t) as a proxy. Stock changes are ignored."})
        supplier = purchases.get(cn, {}).get("supplier", "")
        p = PrecursorInput(id=f"P{cn}", cn=cn, description=_clean_desc(desc or "") or "Wire rod", consumed_t=round(t, 6), supplier=supplier, evidence=ev)
        for it, eid in _direct_first(supplier_see, notes, evidence):
            cn_ok = normalise_cn(it.cn_code or cn)[:4] == cn
            if cn_ok and it.quantity is not None and p.see_actual is None:
                p.see_actual = float(it.quantity)
                p.see_actual_verified = bool(it.verified)
                p.see_evidence = [eid]
                notes.append({"doc": evidence[eid].doc_name, "note": f"Supplier actual SEE {it.quantity} {it.unit or ''} applied to {cn}."})
        precursors.append(p)
    out.purchases_t = {cn: v["t"] for cn, v in purchases.items()}

    out.calc_input = CalcInput(
        country=country,
        year=int(profile.get("year", 2026)),
        period_start=profile.get("period_start", ""),
        period_end=profile.get("period_end", ""),
        goods=goods,
        fuels=fuels,
        precursors=precursors,
        electricity_mwh=round(elec_mwh, 6),
        electricity_evidence=elec_ev,
        use_supplier_data=True,
        price_quarter=profile.get("price_quarter"),
    )
    out.notes = notes
    out.exports_non_eu = exports_non_eu
    return out


_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_PERIOD_RE = re.compile(rf"\s*[\(\[]?\s*\b{_MONTHS}\b[\s\-'/,.]*\d{{0,4}}\s*[\)\]]?", re.I)
_CN_RE = re.compile(r"\s*\(?\b(?:CN|HS)?\s*\d{4}\s?\d{2}(?:\s?\d{2})?\b\)?", re.I)


def _clean_desc(text: str) -> str:
    """A product label for the whole period: drop the month a register row was dated with and the CN code
    (shown separately), e.g. 'Bolts 7318 15 (Apr-26)' -> 'Bolts'."""
    t = _PERIOD_RE.sub(" ", text or "")
    t = _CN_RE.sub(" ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -,:;")
    return t or (text or "").strip()


def _is_indirect(it: LineItem) -> bool:
    text = f"{it.description or ''} {it.source_quote or ''}".lower()
    return "indirect" in text


def _direct_first(items: list, notes: list, evidence: dict) -> list:
    """Supplier SEE values: drop indirect (electricity) values, which do not count for iron & steel, and
    put values explicitly labelled 'direct' first so the first match per precursor is the right one."""
    kept = []
    for it, eid in items:
        if _is_indirect(it):
            notes.append({"doc": evidence[eid].doc_name, "note": f"Ignored supplier value {it.quantity} {it.unit or ''}: it is an indirect (electricity) figure, and only direct emissions count for iron & steel."})
        else:
            kept.append((it, eid))
    return sorted(kept, key=lambda x: 0 if "direct" in f"{x[0].description or ''} {x[0].source_quote or ''}".lower() else 1)
