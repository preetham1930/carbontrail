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


# CBAM default values are set per 6-digit CN subheading, so goods are matched on 6 digits: an export invoice's
# "7318 15 88" and a register's "7318 15" are the same good. A register that names the product but gives no code
# ("बोल्ट", "Nuts") is mapped from the name, and the note asks the owner to confirm.
_NAME_TO_CN = [
    (re.compile(r"\b(bolts?|screws?)\b|बोल्ट|स्क्रू|स्क्रु|पेंच", re.I), "731815"),
    (re.compile(r"\bnuts?\b|नट", re.I), "731816"),
    (re.compile(r"\bwashers?\b|वॉशर|वाशर", re.I), "731822"),
]


def _cn6(code, desc) -> str:
    cn = normalise_cn(code or "")[:6]
    if len(cn) >= 4:
        return cn
    for rx, c in _NAME_TO_CN:
        if rx.search(desc or ""):
            return c
    return cn


_FIX_WORDS = re.compile(r"\b(correct(ed|ion)?|crossed|struck|overwrit\w*|cut out|scratched)\b", re.I)


def _fix_notes(ex) -> list[list[float]]:
    """Numbers in the reader's notes that describe a correction ("49.8 crossed out and replaced with 50.3")."""
    out = []
    for note in getattr(ex, "reading_notes", None) or []:
        if _FIX_WORDS.search(note or "") or re.search(r"replaced|written above|over-?written|cut", note or "", re.I):
            nums = [float(x.replace(",", "")) for x in re.findall(r"(?<![\d.])\d[\d,]*\.\d+|(?<![\d.,])\d{1,3}(?![\d.,])", note)]
            if len(nums) >= 2:
                out.append(nums)
    return out


def _fix_from_notes(fix_notes, qty) -> str:
    """A figure the reader said was corrected: the value used, next to a close, different number in the same note."""
    if qty is None:
        return ""
    for nums in fix_notes:
        if any(abs(x - qty) < 1e-9 for x in nums):
            others = [x for x in nums if abs(x - qty) > 1e-9 and abs(x - qty) <= 0.1 * abs(qty)]
            if others:
                return f"{others[0]:g} → {qty:g}"
    return ""



def _correction(quote, desc, qty, doc_type="") -> str:
    """A handwritten correction in a register: two close numbers side by side where the value read is one of them
    ("61.5 62.5" read as 62.5), or a line that says it was corrected. Returns "61.5 → 62.5" or "corrected entry", else ""."""
    q = quote or ""
    if qty and doc_type == "production_register":
        for a, b in re.findall(r"(?<![\d.])(\d+\.\d+)\s*(?:→|->|/|\s)\s*(\d+\.\d+)(?![\d.])", q):
            a, b = float(a), float(b)
            old = a if abs(b - qty) < 1e-9 else b if abs(a - qty) < 1e-9 else None
            if old is not None and old != qty and abs(old - qty) <= 0.1 * abs(qty):
                return f"{old:g} → {qty:g}"
    if _FIX_WORDS.search(f"{q} {desc or ''}"):
        m = re.search(r"from\s+(\d+(?:\.\d+)?)", f"{q} {desc or ''}", re.I)
        return f"{float(m.group(1)):g} → {qty:g}" if (m and qty) else "corrected entry"
    return ""


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
    corrections: dict[str, str] = field(default_factory=dict)  # evidence id -> what the correction looks like ("61.5 → 62.5")
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


def _unit_key(unit) -> str:
    from .cbam.engine import _u
    u = _u(unit or "")
    return {"tonne": "t", "tonnes": "t", "ton": "t", "tons": "t", "mt": "t", "kgs": "kg", "ltr": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l"}.get(u, u)


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
    # an unlabelled total: one row equal to the sum of the other rows of the same good, in the same unit
    groups: dict = defaultdict(list)
    for i in rows:
        if i.quantity is not None:
            key = _cn6(i.cn_code, i.description) if kind in ("production", "export", "precursor_consumption", "precursor_purchase") else ""
            groups[(key, _unit_key(i.unit))].append(i)
    drop = []
    for g in groups.values():
        if len(g) < 3:
            continue
        for x in g:
            rest = sum(float(o.quantity) for o in g if o is not x)
            if rest > 0 and abs(float(x.quantity) - rest) <= 0.005 * rest:
                drop.append(x)
                break
    if drop:
        return [i for i in items if not any(i is d for d in drop)], [d.description for d in drop]
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
        fix_notes = _fix_notes(ex)
        for it in items:
            n += 1
            eid = f"E{n}"
            evidence[eid] = Evidence(eid, meta["id"], meta["name"], ex.doc_type, it.page or 1, (it.source_quote or "")[:200],
                                     it.kind, it.description, it.quantity, it.unit, float(it.confidence or 0))
            fix = _correction(it.source_quote, it.description, it.quantity, ex.doc_type) or _fix_from_notes(fix_notes, it.quantity)
            if fix:
                out.corrections[eid] = fix
            if (it.confidence if it.confidence is not None else 1.0) < 0.75 or fix:
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
                    cn = _cn6(it.cn_code, it.description)
                    if not cn:
                        notes.append({"doc": meta["name"], "note": f"Production line '{it.description}' has no CN code; please map it."})
                        continue
                    if not normalise_cn(it.cn_code or ""):
                        notes.append({"doc": meta["name"], "note": f"'{it.description}' has no CN code on the page; mapped to CN {cn[:4]} {cn[4:]} from its name. Please confirm."})
                    t = to_tonnes(it.quantity, it.unit or "t")
                    production[cn]["t"] += t
                    production[cn]["desc"] = production[cn]["desc"] or it.description
                    production[cn]["ev"].append(eid)
                    out.months_with_production |= months
                elif it.kind == "precursor_consumption":
                    cn = normalise_cn(it.cn_code or "7213")[:4]
                    t = to_tonnes(it.quantity, it.unit or "t")
                    consumption[cn]["t"] += t
                    consumption[cn]["desc"] = consumption[cn]["desc"] or it.description
                    consumption[cn]["ev"].append(eid)
                elif it.kind == "precursor_purchase":
                    cn = normalise_cn(it.cn_code or "7213")[:4]
                    t = to_tonnes(it.quantity, it.unit or "t")
                    purchases[cn]["t"] += t
                    purchases[cn]["desc"] = purchases[cn]["desc"] or it.description
                    purchases[cn]["ev"].append(eid)
                    purchases[cn]["supplier"] = purchases[cn]["supplier"] or (it.party or ex.issuer or "")
                elif it.kind == "export":
                    cn = _cn6(it.cn_code, it.description)
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
        if p["t"] <= 0:
            continue
        if not check_scope(cn).in_scope:
            notes.append({"doc": "-", "note": f"{cn} is not a CBAM good; excluded."})
            continue
        exp = exports_eu.get(cn, {"t": 0.0, "ev": []})
        goods.append(GoodInput(cn=cn, description=_good_name(cn, p["desc"]), production_t=round(p["t"], 6), export_eu_t=round(exp["t"], 6), evidence=p["ev"] + exp["ev"]))
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
        name = _precursor_name(desc) or _precursor_name(purchases.get(cn, {}).get("desc")) or ("Wire rod" if cn == "7213" else f"Precursor (CN {cn})")
        p = PrecursorInput(id=f"P{cn}", cn=cn, description=name, consumed_t=round(t, 6), supplier=supplier, evidence=ev)
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


def _good_name(cn: str, desc: str) -> str:
    """The official CN description when we know the code (e.g. 'Threaded screws and bolts, of iron or steel'),
    else the cleaned label from the document."""
    from .cbam.engine import REF
    entry = REF.get("product_defaults", {}).get(normalise_cn(cn).replace(" ", "")[:6])
    return entry["description"] if isinstance(entry, dict) and entry.get("description") else _clean_desc(desc)


def _clean_desc(text: str) -> str:
    """A product label for the whole period: drop the month a register row was dated with and the CN code
    (shown separately), e.g. 'Bolts 7318 15 (Apr-26)' -> 'Bolts'."""
    t = _PERIOD_RE.sub(" ", text or "")
    t = _CN_RE.sub(" ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -,:;")
    return t or (text or "").strip()


_LEDGER_RE = re.compile(r"\b(consumption|consumed|issued|issue|used|usage|purchased|purchase|received|supplied|bought|delivered)\b", re.I)
_NON_LATIN_RE = re.compile(r"[\u0900-\u0DFF\u0E00-\u0FFF\u3000-\u9FFF\uAC00-\uD7AF]")


def _precursor_name(desc: str | None) -> str:
    """'Wire rod consumption (तार रॉड खपत)' -> 'Wire rod': the material in plain English, without the ledger
    verb or a translation in brackets. Empty when nothing English is left (the caller falls back)."""
    t = _clean_desc(desc or "")
    t = re.sub(r"\([^)]*\)", lambda m: " " if _NON_LATIN_RE.search(m.group(0)) else m.group(0), t)
    t = _LEDGER_RE.sub(" ", t)
    t = re.sub(r"\b(?:to|for|in|into)\s+(?:the\s+)?(?:production|shop floor|plant|process|forging)\b", " ", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" -,:;/|")
    if not re.search(r"[A-Za-z]{2}", t) or _NON_LATIN_RE.search(t):
        return ""
    return t[:1].upper() + t[1:]


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
