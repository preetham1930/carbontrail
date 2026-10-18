"""Files that are not business records, and figures outside the reporting period, never reach the calculation."""
import pytest

from app.cbam.engine import CalcError
from app.checks import run_checks
from app.normalize import normalise
from app.pipeline import _nothing_to_calculate
from app.schemas import DocumentExtraction, LineItem

PROFILE = {"country": "India", "year": 2026, "period_start": "2026-04-01", "period_end": "2026-06-30"}


def _item(**kw):
    base = dict(kind="production", description="Bolts", quantity=100.0, unit="t", cn_code="7318 15",
                source_quote="Bolts 100 t", page=1, confidence=0.9)
    base.update(kw)
    return LineItem(**base)


def test_mockup_is_set_aside_and_nothing_is_calculated():
    # a website design with an illustrative register and a data table in it
    ex = DocumentExtraction(is_source_record=False, not_a_record_reason="a website design mock-up with illustrative numbers",
                            doc_type="other", line_items=[_item(), _item(kind="fuel", description="LPG", quantity=1000, unit="kg", fuel_type="lpg")])
    norm = normalise([({"id": "D1", "name": "Untitled.png"}, ex)], PROFILE)
    assert norm.calc_input.goods == [] and norm.calc_input.fuels == []
    assert norm.set_aside == [{"doc": "Untitled.png", "reason": "a website design mock-up with illustrative numbers"}]
    msg = _nothing_to_calculate(norm)
    assert "Untitled.png" in msg and "production register" in msg
    codes = [f["code"] for f in run_checks(norm, PROFILE)]
    assert "not_a_record" in codes


def test_quantities_outside_the_period_are_left_out():
    ex = DocumentExtraction(doc_type="production_register", line_items=[
        _item(period_start="2026-01-01", period_end="2026-01-31", quantity=100.0),   # January: outside Apr-Jun
        _item(period_start="2026-05-01", period_end="2026-05-31", quantity=60.0),
    ])
    norm = normalise([({"id": "D1", "name": "register.jpg"}, ex)], PROFILE)
    assert [g.production_t for g in norm.calc_input.goods] == [60.0]
    assert len(norm.out_of_period) == 1


def test_bill_issued_after_the_period_still_counts():
    ex = DocumentExtraction(doc_type="electricity_bill", document_date="2026-07-05", line_items=[
        _item(kind="electricity", description="Grid kWh", quantity=1000, unit="kWh", cn_code=None)])
    norm = normalise([({"id": "D1", "name": "bill.pdf"}, ex)], PROFILE)
    assert norm.out_of_period == []
    assert norm.calc_input.electricity_mwh == pytest.approx(1.0)


def test_agent_finding_merges_with_the_check_it_explains():
    from app.agents.factory import match_covers
    auto = [{"code": "electricity_gap", "severity": "low"}, {"code": "precursor_default", "severity": "high"},
            {"code": "low_confidence", "severity": "medium"}, {"code": "dg_excluded", "severity": "info"}]
    # a named code wins; unknown codes are ignored
    assert match_covers({"title": "Anything"}, "precursor_default, bogus", auto, set()) == ["precursor_default"]
    # no code named: the title decides (titles Gemini wrote on a real run)
    assert match_covers({"title": "Precursor Supplier Emissions Data (SEE) Missing for Wire Rod"}, "", auto, set()) == ["precursor_default"]
    assert match_covers({"title": "Low-Confidence Reading on May 2026 Bolt Production Figure"}, "", auto, set()) == ["low_confidence"]
    assert match_covers({"title": "Missing Electricity Bills for April and May 2026"}, "", auto, set()) == ["electricity_gap"]
    # a check already merged into another finding is not taken twice; an unrelated finding merges with nothing
    assert match_covers({"title": "Wire rod again"}, "", auto, {"precursor_default"}) == []
    assert match_covers({"title": "Export prices look low"}, "", auto, set()) == []


def test_handwritten_correction_is_flagged_even_when_read_confidently():
    from app.normalize import _correction
    reg = "production_register"
    assert _correction("61.5 62.5", "Bolts (7318 15)", 62.5, reg) == "61.5 → 62.5"
    assert _correction("Bolts (May-26) - corrected from 61.5", "Bolts", 62.5, reg) == "61.5 → 62.5"
    assert _correction("Apr-26 60.1", "Bolts (7318 15)", 60.1, reg) == ""
    assert _correction("May-26 89.9 62.5", "Bolts", 62.5, reg) == ""          # different columns, not a correction
    assert _correction("120.0 118.4", "Bolts", 120.0, "export_invoice") == ""  # only registers are handwritten


def test_corrected_register_line_becomes_a_finding():
    ex = DocumentExtraction(doc_type="production_register", line_items=[
        _item(period_start="2026-05-01", period_end="2026-05-31", quantity=62.5, source_quote="61.5 62.5", confidence=0.95)])
    norm = normalise([({"id": "D1", "name": "07_register.jpg"}, ex)], PROFILE)
    f = [f for f in run_checks(norm, PROFILE) if f["code"] == "low_confidence"]
    assert len(f) == 1 and f[0]["title"] == "A handwritten figure was corrected"
    assert "61.5 → 62.5" in f[0]["detail"] and "used 62.5 t" in f[0]["detail"]


def test_own_uploads_take_period_and_company_from_the_documents():
    from app.profile_infer import infer_profile
    reg = DocumentExtraction(doc_type="production_register", issuer="Shree Balaji Fasteners Pvt. Ltd.", line_items=[
        _item(period_start="2026-07-01", period_end="2026-07-31"), _item(period_start="2026-08-01", period_end="2026-08-31")])
    fuel = DocumentExtraction(doc_type="fuel_invoice", issuer="Rajkot Petro", recipient="Shree Balaji Fasteners Pvt. Ltd.",
                              document_date="2026-09-05", line_items=[_item(kind="fuel", description="FO", quantity=9000, unit="L", fuel_type="furnace_oil")])
    old = DocumentExtraction(doc_type="export_invoice", issuer="Shree Balaji Fasteners Pvt. Ltd.", document_date="2026-04-10",
                             line_items=[_item(kind="export", quantity=10, period_start=None)])
    prof, notes = infer_profile({**PROFILE, "auto": True, "company": "Your factory"}, [reg, fuel, old])
    assert (prof["period_start"], prof["period_end"], prof["price_quarter"]) == ("2026-07-01", "2026-09-30", "2026-Q3")
    assert prof["company"] == "Shree Balaji Fasteners Pvt. Ltd."
    assert "Q3 2026" in notes[0] and "1 dated figure fall" in notes[0]
    # the bundled demo keeps its fixed profile
    assert infer_profile(PROFILE, [reg])[0] == PROFILE


def test_hindi_register_without_codes_and_8_digit_export_codes_match_up():
    reg = DocumentExtraction(doc_type="production_register", period_start="2026-04-01", period_end="2026-06-30", line_items=[
        _item(description="बोल्ट (टन) अप्रैल", cn_code=None, quantity=48.6, unit="टन", period_start="2026-04-01", period_end="2026-04-30"),
        _item(description="बोल्ट (टन) मई", cn_code=None, quantity=50.3, unit="टन", period_start="2026-05-01", period_end="2026-05-31"),
        _item(description="बोल्ट (टन) जून", cn_code=None, quantity=46.9, period_start="2026-06-01", period_end="2026-06-30"),
        _item(description="बोल्ट (टन)", cn_code=None, quantity=145.8, period_start="2026-04-01", period_end="2026-06-30"),  # unlabelled total
        _item(description="नट (टन) अप्रैल", cn_code=None, quantity=16.2, period_start="2026-04-01", period_end="2026-04-30")])
    exp = DocumentExtraction(doc_type="export_invoice", document_date="2026-04-30", line_items=[
        _item(kind="export", description="Hex bolts M12 DIN 933", cn_code="7318 15 88", quantity=24600, unit="kg", destination_country="Germany")])
    norm = normalise([({"id": "D1", "name": "register.jpg"}, reg), ({"id": "D2", "name": "inv.png"}, exp)], PROFILE)
    goods = {g.cn: g for g in norm.calc_input.goods}
    assert round(goods["731815"].production_t, 1) == 145.8 and goods["731815"].export_eu_t == 24.6
    assert round(goods["731816"].production_t, 1) == 16.2
    assert any("mapped to CN 7318 15" in n["note"] for n in norm.notes)


def test_correction_described_only_in_the_readers_notes_is_still_flagged():
    ex = DocumentExtraction(doc_type="production_register", reading_notes=[
        "Row for May 2026 bolt production has an original entry '49.8' crossed out and corrected to '50.3' written above it."],
        line_items=[_item(period_start="2026-05-01", period_end="2026-05-31", quantity=50.3, source_quote="50.3", confidence=0.95),
                    _item(period_start="2026-04-01", period_end="2026-04-30", quantity=48.6, source_quote="48.6", confidence=0.95)])
    norm = normalise([({"id": "D1", "name": "register.jpg"}, ex)], PROFILE)
    f = [f for f in run_checks(norm, PROFILE) if f["code"] == "low_confidence"]
    assert len(f) == 1 and "49.8 → 50.3" in f[0]["detail"]


def test_precursor_name_is_plain_english():
    from app.normalize import _precursor_name
    assert _precursor_name("Wire rod consumption (तार रॉड खपत)") == "Wire rod"
    assert _precursor_name("Wire rod issued") == "Wire rod"
    assert _precursor_name("तार रॉड खपत") == ""


def test_pdf_text_is_escaped_and_hindi_gets_its_font():
    from app.exports import _px
    assert _px("M&S <rod>") == "M&amp;S &lt;rod&gt;"
    out = _px("मई 2026 | 50.3 टन")
    assert "NotoDeva" in out and "50.3" in out
