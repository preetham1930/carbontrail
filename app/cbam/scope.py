"""CBAM scope check: is a CN code covered, and what are the rules for it.

Coverage follows Annex I of Regulation (EU) 2023/956. Aggregated goods
categories and the direct-only rule follow Annex II and the methodology in
IR (EU) 2025/2547. This module deliberately covers the iron & steel and
aluminium sectors in depth (the CarbonTrail beachhead) and the other sectors
at heading level.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# (prefix, sector, aggregated goods category)
_RULES: list[tuple[str, str, str]] = [
    # Iron & steel
    ("260112", "iron_steel", "Sintered ore"),
    ("7201", "iron_steel", "Pig iron"),
    ("72021", "iron_steel", "FeMn"),
    ("72024", "iron_steel", "FeCr"),
    ("72026", "iron_steel", "FeNi"),
    ("7203", "iron_steel", "DRI"),
    ("7206", "iron_steel", "Crude steel"),
    ("7207", "iron_steel", "Crude steel"),
    ("7218", "iron_steel", "Crude steel"),
    ("7224", "iron_steel", "Crude steel"),
    *[(f"72{n:02d}", "iron_steel", "Iron or steel products") for n in range(8, 30) if n not in (18, 24)],
    *[(f"73{n:02d}", "iron_steel", "Iron or steel products") for n in range(1, 12)],
    ("7318", "iron_steel", "Iron or steel products"),
    ("7326", "iron_steel", "Iron or steel products"),
    # Aluminium
    ("7601", "aluminium", "Unwrought aluminium"),
    *[(f"76{n:02d}", "aluminium", "Aluminium products") for n in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16)],
    # Other sectors (heading level)
    ("250700", "cement", "Calcined clay"),
    ("2523", "cement", "Cement / clinker / aluminous cement"),
    ("2808", "fertilisers", "Nitric acid"),
    ("2814", "fertilisers", "Ammonia"),
    ("283421", "fertilisers", "Potassium nitrate"),
    ("3102", "fertilisers", "Fertilisers"),
    ("3105", "fertilisers", "Mixed fertilisers"),
    ("28041000", "hydrogen", "Hydrogen"),
    ("2716", "electricity", "Electricity"),
]

# Sectors where only direct emissions count (Annex II): iron & steel, aluminium, hydrogen.
_DIRECT_ONLY = {"iron_steel", "aluminium", "hydrogen"}

_PRECURSORS = {
    "Iron or steel products": ["Crude steel", "Iron or steel products (e.g. wire rod, bars)", "Pig iron", "DRI", "FeMn", "FeCr", "FeNi"],
    "Aluminium products": ["Unwrought aluminium", "Aluminium products"],
    "Crude steel": ["Pig iron", "DRI", "Sintered ore", "FeMn", "FeCr", "FeNi"],
}

_SECTOR_LABEL = {
    "iron_steel": "Iron and steel",
    "aluminium": "Aluminium",
    "cement": "Cement",
    "fertilisers": "Fertilisers",
    "hydrogen": "Hydrogen",
    "electricity": "Electricity",
}


@dataclass
class ScopeResult:
    cn_code: str
    in_scope: bool
    sector: str | None = None
    sector_label: str | None = None
    aggregated_category: str | None = None
    emissions_counted: str | None = None
    relevant_precursors: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalise_cn(cn: str) -> str:
    return re.sub(r"\D", "", str(cn or ""))


def check_scope(cn_code: str) -> ScopeResult:
    cn = normalise_cn(cn_code)
    if len(cn) < 4:
        return ScopeResult(cn_code=cn_code, in_scope=False, note="CN code too short to classify; need at least 4 digits.")
    if cn.startswith("7204"):
        return ScopeResult(cn_code=cn, in_scope=False, note="Ferrous waste and scrap (7204) is not a CBAM good.")
    best: tuple[str, str, str] | None = None
    for prefix, sector, cat in _RULES:
        if cn.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, sector, cat)
    if best is None:
        return ScopeResult(
            cn_code=cn,
            in_scope=False,
            note="Not listed in Annex I of Regulation (EU) 2023/956. Check the 2028 downstream-extension proposal if this is a steel- or aluminium-intensive product.",
        )
    _, sector, cat = best
    direct_only = sector in _DIRECT_ONLY
    return ScopeResult(
        cn_code=cn,
        in_scope=True,
        sector=sector,
        sector_label=_SECTOR_LABEL[sector],
        aggregated_category=cat,
        emissions_counted="Direct emissions only (Annex II)" if direct_only else "Direct and indirect emissions",
        relevant_precursors=_PRECURSORS.get(cat, []),
        note="Embedded emissions of relevant precursors must be added to the good's own direct emissions.",
    )
