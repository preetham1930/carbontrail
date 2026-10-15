"""Hand-checked worked example for the demo installation (Q2 2026).

Every expected value below was computed by hand from the formulas in
IR (EU) 2025/2547 and the IPCC 2006 defaults, independently of the engine.
"""
import math

import pytest

from app.cbam.engine import (CalcInput, FuelInput, GoodInput, PrecursorInput, calculate,
                             run_scenario, country_comparison, CalcError)
from app.cbam.scope import check_scope


def demo_input(supplier_see=None):
    return CalcInput(
        country="India", year=2026, period_start="2026-04-01", period_end="2026-06-30",
        goods=[GoodInput("7318 15", "Hex bolts", 182.4, 120.0), GoodInput("731816", "Hex nuts", 61.6, 40.0)],
        fuels=[
            FuelInput("f1", "lpg", 19800, "kg", "process_heat"),
            FuelInput("f2", "lpg", 20100, "kg", "process_heat"),
            FuelInput("f3", "lpg", 18.5, "MT", "process_heat"),
            FuelInput("f4", "diesel", 9600, "L", "electricity_generation"),
        ],
        precursors=[PrecursorInput("p1", "7213", "Wire rod", 262.3, "Mill",
                                   see_actual=supplier_see, see_actual_verified=supplier_see is not None)],
        electricity_mwh=412.6,
        price_quarter="2026-Q3",
    )


def test_lpg_combustion():
    r = calculate(demo_input())
    lpg = sum(f["co2_t"] for f in r["fuels"] if f["fuel"] == "lpg")
    # 58.4 t x 47.3 GJ/t = 2762.32 GJ = 2.76232 TJ x 63.1 = 174.302392 t
    assert math.isclose(lpg, 174.302392, rel_tol=1e-9)
    assert math.isclose(r["attributed_direct_t"], 174.302392, rel_tol=1e-9)


def test_dg_diesel_excluded_for_steel():
    r = calculate(demo_input())
    dg = [f for f in r["fuels"] if f["fuel"] == "diesel"][0]
    # 9.6 kL x 0.835 = 8.016 t x 43.0 = 344.688 GJ x 74.1 / 1000 = 25.5413808 t
    assert math.isclose(dg["co2_t"], 25.5413808, rel_tol=1e-9)
    assert dg["counted"] is False
    assert math.isclose(r["excluded_t"], 25.5413808, rel_tol=1e-9)


def test_see_with_default_precursor():
    r = calculate(demo_input())
    # (174.302392 + 262.3 x 4.32) / 244.0 = (174.302392 + 1133.136) / 244.0
    expected = (174.302392 + 1133.136) / 244.0
    assert math.isclose(r["see"], expected, rel_tol=1e-9)
    assert round(r["see"], 4) == 5.3584


def test_see_with_supplier_data():
    r = calculate(demo_input(supplier_see=2.36))
    expected = (174.302392 + 262.3 * 2.36) / 244.0
    assert math.isclose(r["see"], expected, rel_tol=1e-9)
    assert round(r["see"], 4) == 3.2514


def test_costs_and_saving():
    r = calculate(demo_input(supplier_see=2.36))
    see = r["see"]
    price = 82.32
    assert r["price"]["eur_per_t"] == price
    actual = 160.0 * see * price
    default = (120.0 * 5.72 * 1.10 + 40.0 * 5.14 * 1.10) * price
    assert math.isclose(r["totals"]["gross_cost_actual_eur"], actual, rel_tol=1e-9)
    assert math.isclose(r["totals"]["gross_cost_default_eur"], default, rel_tol=1e-9)
    assert math.isclose(r["totals"]["saving_eur"], default - actual, rel_tol=1e-9)
    # Sanity: EUR 80.8k gross exposure on defaults vs EUR 42.8k on actual data per quarter
    assert 37500 < r["totals"]["saving_eur"] < 38500


def test_scenarios():
    inp = demo_input(supplier_see=2.36)
    s = run_scenario(inp, {"precursor_see": 0.9})
    assert s["delta_see"] < 0
    s2 = run_scenario(inp, {"fuel_switch_to": "natural_gas"})
    # Same heat with natural gas: 2762.32 GJ x 56.1 / 1000 = 154.966 t (vs 174.302 t)
    lpg_gj = 58.4 * 47.3
    new = lpg_gj / 1000 * 56.1
    assert math.isclose(s2["result"]["attributed_direct_t"], new, rel_tol=1e-9)
    s3 = run_scenario(inp, {"renewable_electricity_pct": 100})
    assert s3["delta_see"] == 0


def test_scope():
    assert check_scope("7318 15").in_scope
    assert check_scope("7318 15").emissions_counted.startswith("Direct")
    assert not check_scope("7204 10").in_scope
    assert not check_scope("8708 99").in_scope
    assert check_scope("7213 91 10").aggregated_category == "Iron or steel products"


def test_country_comparison_sorted():
    rows = country_comparison("731815")
    assert rows[0]["default"] <= rows[-1]["default"]
    india = [r for r in rows if r["country"] == "India"][0]
    assert math.isclose(india["default_2026"], 6.292, rel_tol=1e-9)


def test_errors():
    with pytest.raises(CalcError):
        calculate(CalcInput("India", 2026, "", "", goods=[]))
