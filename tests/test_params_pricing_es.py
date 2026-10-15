"""Parameter integrity, sensitivity definitions, classification rules, ES arithmetic."""
import math

import numpy as np
import pytest

from frtb import es, pricing, sbm
from frtb.config import SQL_DIR, TENORS
from frtb.sensitivities import position_sensitivities


def test_table1_risk_weights_from_standard(make_db):
    conn = make_db([("D", 1, "FX", "EUR", None, 1.0)])
    rw = dict(conn.execute("SELECT tenor_years, risk_weight FROM girr_tenor_rw"))
    assert [rw[t] for t in TENORS] == [0.017, 0.017, 0.016, 0.013, 0.012, 0.011, 0.011, 0.011, 0.011, 0.011]


def test_table2_matches_footnote13_formula(make_db):
    conn = make_db([("D", 1, "FX", "EUR", None, 1.0)])
    corr = {(a, b): r for a, b, r in conn.execute("SELECT tenor_k, tenor_l, rho FROM girr_tenor_corr")}
    for a in TENORS:
        for b in TENORS:
            f = max(math.exp(-0.03 * abs(a - b) / min(a, b)), 0.40)
            assert corr[(a, b)] == pytest.approx(f, abs=6e-4)     # table is rounded to 0.1%
    assert corr[(1.0, 5.0)] == pytest.approx(0.887, abs=1e-9)    # MAR21.46 fn13 worked example (88.69%)


def test_all_scalar_parameters_verified_in_text(make_db):
    conn = make_db([("D", 1, "FX", "EUR", None, 1.0)])
    assert conn.execute("SELECT param_name FROM sbm_parameters WHERE verified_in_text <> 'Y'").fetchall() == []


def test_scenario_formulas():
    p = dict(scen_high_mult=1.25, scen_high_cap=1.0, scen_low_a=2.0, scen_low_b=0.75)
    assert sbm.scale_corr(0.5, "high", p) == pytest.approx(0.625)
    assert sbm.scale_corr(0.9, "high", p) == pytest.approx(1.0)       # capped
    assert sbm.scale_corr(0.5, "low", p) == pytest.approx(0.375)      # 75% branch
    assert sbm.scale_corr(0.97, "low", p) == pytest.approx(0.94)      # 2x-1 branch
    assert sbm.scale_corr(0.6, "low", p) == pytest.approx(0.45)


def test_girr_sensitivity_zero_coupon_at_vertex():
    # 5y zero-coupon bond, flat 4% curve: V = N e^{-0.04*5}; bump the 5y vertex by 1bp
    curves = {c: {t: 0.04 for t in TENORS} for c in ("USD", "EUR")}
    inst = dict(instr_type="BOND", currency="USD", notional=1e6, rate=0.0, maturity_years=5.0)
    rows = position_sensitivities(inst, 1.0, curves, {"USD": 1.0, "EUR": 1.1})
    v = 1e6 * math.exp(-0.2)
    s5 = [r for r in rows if r["risk_factor"] == "USD_GIRR_5Y"][0]
    assert s5["sensitivity"] == pytest.approx(v * (math.exp(-5e-4) - 1) / 1e-4, rel=1e-9)   # exact 1bp bump
    assert s5["sensitivity"] == pytest.approx(-5 * v, rel=1e-3)       # ~ dV/dr = -T*V
    assert s5["pv01_usd"] == pytest.approx(-5 * v * 1e-4, rel=1e-3)
    assert {r["risk_factor"] for r in rows} == {"USD_GIRR_5Y"}       # nothing leaks to other vertices


def test_fx_delta_is_usd_pv_of_foreign_exposure():
    curves = {c: {t: 0.03 for t in TENORS} for c in ("USD", "EUR")}
    inst = dict(instr_type="BOND", currency="EUR", notional=1e6, rate=0.0, maturity_years=1.0)
    rows = position_sensitivities(inst, 1.0, curves, {"USD": 1.0, "EUR": 1.2})
    fx = [r for r in rows if r["risk_class"] == "FX"][0]
    assert fx["sensitivity"] == pytest.approx(1e6 * math.exp(-0.03) * 1.2, rel=1e-9)


def test_fx_forward_delta_and_rate_legs():
    curves = {c: {t: 0.03 for t in TENORS} for c in ("USD", "EUR")}
    inst = dict(instr_type="FXFWD", currency="EUR", notional=1e6, rate=1.2, maturity_years=1.0)
    rows = position_sensitivities(inst, 1.0, curves, {"USD": 1.0, "EUR": 1.2})
    fx = [r for r in rows if r["risk_class"] == "FX"][0]
    assert fx["sensitivity"] == pytest.approx(1e6 * 1.2 * math.exp(-0.03), rel=1e-9)   # foreign leg PV in USD
    assert {r["bucket"] for r in rows if r["risk_class"] == "GIRR"} == {"USD", "EUR"}   # both legs


def test_irs_par_swap_has_zero_value_and_signs():
    curve = np.full((1, 10), 0.03)
    df = np.exp(-0.03 * np.arange(1.0, 6.0))
    par = (1 - df[-1]) / df.sum()
    assert pricing.pv_irs_receive_fixed(curve, 1e6, par, 5.0)[0] == pytest.approx(0.0, abs=1e-6)
    assert pricing.pv_irs_receive_fixed(curve + 1e-4, 1e6, par, 5.0)[0] < 0   # receive-fixed loses when rates rise


def test_book_classification_rules(make_db):
    conn = make_db([("D", 1, "FX", "EUR", None, 1.0)])
    for t in ("sensitivities", "positions", "books"):
        conn.execute(f"DELETE FROM {t}")
    cases = [("a", "market_making", "FVTPL_TRADING", 10, 0, 1),
             ("b", "hold_to_maturity", "AMORTISED_COST", 900, 0, 0),
             ("c", "alm_accrual", "FVTPL_TRADING", 900, 0, 1),     # RBC25.9(1) presumption
             ("d", "arbitrage", "FVTPL_TRADING", 10, 1, 0),        # RBC25.3 legal impediment wins
             ("e", "short_term_resale", "FVOCI", 500, 0, 1)]
    for i, (n, pur, acct, hold, imp, _) in enumerate(cases, 1):
        conn.execute("INSERT INTO books (book_id,desk_id,book_name,stated_purpose,accounting_class,"
                     "expected_holding_days,legal_impediment) VALUES (?,1,?,?,?,?,?)", (i, n, pur, acct, hold, imp))
    conn.execute((SQL_DIR / "10_book_classification.sql").read_text(encoding="utf-8"))
    assert dict(conn.execute("SELECT book_name, trading_book_flag FROM books")) == {c[0]: c[5] for c in cases}
    note = dict(conn.execute("SELECT book_name, review_note FROM books"))
    assert note["e"].startswith("REVIEW") and note["c"].startswith("REVIEW") and note["a"] is None


def test_expected_shortfall_arithmetic():
    assert es.expected_shortfall(np.arange(1.0, 101.0)) == pytest.approx(99.0)   # worst ceil(2.5)=3: 98,99,100
    assert es.expected_shortfall(np.arange(1.0, 41.0)) == pytest.approx(40.0)    # 40 obs -> k=1


def test_lh_cascade_formula():
    # sqrt(ES1^2 + (ES2*sqrt((20-10)/10))^2) with ES1=3, ES2=4 -> 5
    assert math.sqrt(3**2 + (4 * math.sqrt((20 - 10) / 10)) ** 2) == pytest.approx(5.0)
