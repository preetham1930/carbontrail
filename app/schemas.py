"""Structured output schemas for the Gemini document-intake agent."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DocType = Literal[
    "electricity_bill",
    "fuel_invoice",
    "precursor_purchase_invoice",
    "production_register",
    "export_invoice",
    "supplier_emissions_declaration",
    "other",
]

ItemKind = Literal[
    "fuel",
    "electricity",
    "precursor_purchase",
    "precursor_consumption",
    "production",
    "export",
    "scrap",
    "supplier_see",
    "other",
]

FuelType = Literal["lpg", "natural_gas", "diesel", "furnace_oil", "petrol", "coal", "other"]
FuelUse = Literal["process_heat", "electricity_generation", "mobile", "unknown"]


class LineItem(BaseModel):
    kind: ItemKind = Field(description="What this quantity represents for CBAM.")
    description: str = Field(description="Short description as written on the document, translated to English if needed.")
    quantity: Optional[float] = Field(default=None, description="Numeric quantity exactly as printed (no unit conversion).")
    unit: Optional[str] = Field(default=None, description="Unit exactly as printed, e.g. kg, MT, L, kWh, SCM, tCO2e/t.")
    cn_code: Optional[str] = Field(default=None, description="CN/HS code if printed or clearly implied (e.g. 7318 15, 7213 91).")
    fuel_type: Optional[FuelType] = Field(default=None, description="Only for kind=fuel.")
    fuel_use: Optional[FuelUse] = Field(default=None, description="Only for kind=fuel: what the fuel is burned for, if the document says (furnace, DG set, vehicle).")
    destination_country: Optional[str] = Field(default=None, description="Only for kind=export: country of the consignee, in English.")
    party: Optional[str] = Field(default=None, description="Supplier, mill or buyer name if relevant.")
    verified: Optional[bool] = Field(default=None, description="Only for kind=supplier_see: true if the document states the value was verified by an accredited verifier.")
    period_start: Optional[str] = Field(default=None, description="Start of the period this quantity covers, YYYY-MM-DD.")
    period_end: Optional[str] = Field(default=None, description="End of the period this quantity covers, YYYY-MM-DD.")
    source_quote: str = Field(description="Exact text copied from the document that contains this number (max ~120 characters).")
    page: int = Field(default=1, description="1-based page number where the quote appears.")
    confidence: float = Field(default=0.9, description="0-1 confidence that the quantity and unit were read correctly.")


class DocumentExtraction(BaseModel):
    is_source_record: bool = Field(default=True, description=(
        "Judge the form of the file, not whether the company is real. True when the file IS a business document: "
        "an invoice, bill, statement, register or logbook page, delivery or export document, or an emissions "
        "declaration, including scans and phone photos, and including documents marked sample, specimen, demo or fictional. "
        "False when the file is a screen or a design rather than a document: a screenshot of a website, app or dashboard, "
        "a design mock-up or landing page, a slide, brochure or poster, a chart, or a photo of something that is not a document. "
        "A picture of an invoice or register shown inside such a page is still not a record."))
    not_a_record_reason: Optional[str] = Field(default=None, description="When is_source_record is false: one short sentence saying what the file is instead.")
    doc_type: DocType
    issuer: Optional[str] = None
    recipient: Optional[str] = None
    document_number: Optional[str] = None
    document_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    period_start: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    period_end: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    languages: list[str] = Field(default_factory=list, description="Languages/scripts seen on the document.")
    line_items: list[LineItem] = Field(default_factory=list)
    reading_notes: list[str] = Field(default_factory=list, description="Anything hard to read, handwritten corrections, missing pages, or ambiguities.")
