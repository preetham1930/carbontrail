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
