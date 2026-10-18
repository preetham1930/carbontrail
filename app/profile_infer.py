"""For a factory's own uploads: take the reporting quarter and the company name from the documents themselves.

The bundled demo runs keep their fixed profile. When someone uploads their own paperwork we do not know the
period or the company in advance, so we read them off the documents: the quarter most of the dated figures
fall in, and the name that appears as buyer on bills and as seller on export invoices.
"""
from __future__ import annotations

import calendar
from collections import Counter
from datetime import date

from .schemas import DocumentExtraction

# kinds of figure that belong to a reporting period (a supplier's annual declaration does not)
_DATED_KINDS = {"production", "precursor_consumption", "precursor_purchase", "fuel", "electricity", "export"}
# whose name is the factory: the buyer on what it purchases, the seller on what it ships or records itself
_BUYER_DOCS = {"electricity_bill", "fuel_invoice", "precursor_purchase_invoice"}
_SELLER_DOCS = {"export_invoice", "production_register"}


def _parse(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _quarter(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def _clean(name: str | None) -> str:
    return " ".join((name or "").split()).strip(" ,;:-")


def infer_profile(profile: dict, extractions: list[DocumentExtraction]) -> tuple[dict, list[str]]:
    """Return (profile, notes). Only acts when the profile is marked auto (a factory's own uploads)."""
    if not profile.get("auto"):
        return profile, []
    docs = [ex for ex in extractions if ex is not None and getattr(ex, "is_source_record", True)]
    out, notes = dict(profile), []

    quarters: Counter = Counter()
    for ex in docs:
        if ex.doc_type == "supplier_emissions_declaration":
            continue
        for it in ex.line_items:
            if it.kind not in _DATED_KINDS:
                continue
            d = _parse(it.period_end) or _parse(it.period_start) or _parse(ex.period_end) or _parse(ex.document_date)
            if d:
                quarters[_quarter(d)] += 1
    if quarters:
        # the quarter most figures fall in; on a tie, the later one
        (y, q), n = max(quarters.items(), key=lambda kv: (kv[1], kv[0]))
        m0 = 3 * (q - 1) + 1
        start, end = date(y, m0, 1), date(y, m0 + 2, calendar.monthrange(y, m0 + 2)[1])
        out.update(period_start=start.isoformat(), period_end=end.isoformat(),
                   year=max(2026, y), price_quarter=f"{y}-Q{q}" if y >= 2026 else None)
        label = f"Q{q} {y} ({start.strftime('%b')}–{end.strftime('%b')})"
        others = sum(quarters.values()) - n
        notes.append(f"Reporting period set to {label} from the dates on your documents"
                     + (f"; {others} dated figure{'s' if others != 1 else ''} fall outside it." if others else "."))
        if y < 2026:
            notes.append("These documents are from before 2026, so costs are shown at the 2026 CBAM rules and price.")

    names: Counter = Counter()
    for ex in docs:
        who = ex.recipient if ex.doc_type in _BUYER_DOCS else ex.issuer if ex.doc_type in _SELLER_DOCS else None
        if _clean(who):
            names[_clean(who)] += 1
    if names:
        company, _ = names.most_common(1)[0]
        out["company"] = company
        notes.append(f"Company taken from your documents: {company}" + ("" if company.endswith(".") else "."))
    return out, notes
