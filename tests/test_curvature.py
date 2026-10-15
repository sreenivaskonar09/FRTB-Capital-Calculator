"""GIRR curvature: hand-checkable aggregation cases (SQL path), definition checks, option pricing sanity."""
import math

import numpy as np
import pytest

from frtb import pipeline, pricing, reconcile, sbm
from frtb.config import TENORS
from frtb.sensitivities import position_curvature, position_sensitivities

D = "DESK"
FLAT = {c: {t: 0.04 for t in TENORS} for c in ("USD", "EUR")}
FX = {"USD": 1.0, "EUR": 1.1}


def curv_db(make_db, cvrs, option=True, rows=None):
    """cvrs: list of (bucket, cvr_up, cvr_dn); one position each, all in one trading book.
    rows: optional delta sensitivity rows for make_db (default: a zero FX row to create the book)."""
    conn = make_db(rows or [(D, 1, "FX", "EUR", None, 0.0)])
    conn.execute("UPDATE instruments SET has_optionality = ?", (1 if option else 0,))
    book = conn.execute("SELECT book_id FROM books").fetchone()[0]
    for n, (b, up, dn) in enumerate(cvrs, 100):
        conn.execute("INSERT INTO positions VALUES (?,?,1,1.0,'2026-01-01')", (n, book))
        conn.execute("INSERT INTO curvature_cvr VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (n, b, f"{b}_GIRR_CURV", 0.017, 0.0, 0.0, 0.0, 0.0, up, dn))
    conn.commit()
    return conn


def curv_charges(conn, scope="FIRM", cmode="OPT"):
    sbm.run_basis(conn, "ALL")
    return {s: c for s, c in conn.execute(
        "SELECT scenario, curvature_charge FROM res_curvature_scenario WHERE basis='ALL' AND scope=? AND cmode=?",
        (scope, cmode))}


def test_single_bucket_charge_is_larger_of_up_and_down_loss(make_db):
    ch = curv_charges(curv_db(make_db, [("USD", 100.0, -50.0)]))
    assert ch == pytest.approx({"high": 100.0, "medium": 100.0, "low": 100.0})   # one factor: correlations irrelevant


def test_two_buckets_squared_gamma_in_each_scenario(make_db):
    # USD: up=100, dn=-50 -> K=100 (up, S=100) ; EUR: up=60, dn=80 -> K=80 (down, S=80)
    ch = curv_charges(curv_db(make_db, [("USD", 100.0, -50.0), ("EUR", 60.0, 80.0)]))
    f = lambda g: math.sqrt(100**2 + 80**2 + 2 * g * 100 * 80)
    assert ch["medium"] == pytest.approx(f(0.25))                        # 50%^2 (MAR21.101)
    assert ch["high"] == pytest.approx(f(min(1.25 * 0.25, 1)))           # 0.3125: scenario applied AFTER squaring
    assert ch["low"] == pytest.approx(f(max(2 * 0.25 - 1, 0.75 * 0.25)))  # 0.1875


def test_gamma_is_squared_before_scenario_not_after():
    p = dict(girr_gamma_across_ccy=0.5, curv_corr_power=2.0, scen_high_mult=1.25, scen_high_cap=1.0,
             scen_low_a=2.0, scen_low_b=0.75)
    assert sbm.curvature_gamma(p, "medium") == pytest.approx(0.25)
    assert sbm.curvature_gamma(p, "high") == pytest.approx(0.3125)       # not (1.25*0.5)^2 = 0.390625
    assert sbm.curvature_gamma(p, "low") == pytest.approx(0.1875)        # not max(0,0.375)^2 = 0.140625


def test_psi_zero_when_both_buckets_negative(make_db):
    ch = curv_charges(curv_db(make_db, [("USD", -10.0, -20.0), ("EUR", -5.0, -1.0)]))
    assert ch == pytest.approx({"high": 0.0, "medium": 0.0, "low": 0.0})


def test_psi_one_when_only_one_bucket_negative(make_db):
    # USD: S=+100 (K=100) ; EUR: up=-5, dn=-1 -> K=0, down selected (-1 > -5 is false for up), S=-1
    ch = curv_charges(curv_db(make_db, [("USD", 100.0, -50.0), ("EUR", -5.0, -1.0)]))
    assert ch["medium"] == pytest.approx(math.sqrt(100**2 + 2 * 0.25 * 100 * (-1.0)))


def test_selection_tie_rule_prefers_up_when_cvr_up_larger():
    # both K = 0 (no loss in either direction); MAR21.5(3)(a)(iii): up if sum CVR+ > sum CVR-
    _, d = sbm.curvature_charge({"USD": (-3.0, -7.0)}, 0.25)
    assert d[0]["selected"] == "up" and d[0]["sb"] == -3.0
    _, d = sbm.curvature_charge({"USD": (-7.0, -3.0)}, 0.25)
    assert d[0]["selected"] == "down" and d[0]["sb"] == -3.0


def test_required_scope_is_options_only_and_holistic_variant_is_separate(make_db):
    conn = curv_db(make_db, [("USD", 100.0, -50.0)], option=False)     # instrument has NO optionality
    sbm.run_basis(conn, "ALL")
    opt = conn.execute("SELECT COUNT(*) FROM res_curvature_scenario WHERE cmode='OPT'").fetchone()[0]
    allm = dict(conn.execute("SELECT scenario, curvature_charge FROM res_curvature_scenario "
                             "WHERE cmode='ALL' AND scope='FIRM'"))
    assert opt == 0                                                    # linear instrument: no curvature required
    assert allm["medium"] == pytest.approx(100.0)                      # MAR21.2(4) variant picks it up


def test_total_adds_curvature_to_delta(make_db):
    conn = curv_db(make_db, [("USD", 100.0, -50.0)], rows=[(D, 1, "GIRR", "USD", 5.0, 1e7)])
    sbm.run_basis(conn, "ALL")
    tot = conn.execute("SELECT charge_at_binding FROM res_sbm_final WHERE scope='FIRM' AND risk_class='TOTAL'"
                       ).fetchone()[0]
    assert tot == pytest.approx(110_000.0 + 100.0)                     # 1.1% * 1e7 delta + curvature


# ------------------------------------------------------------------ definitions on real pricing code
def _cvr(inst, qty):
    drows = position_sensitivities(inst, qty, FLAT, FX)
    return position_curvature(inst, qty, FLAT, FX, {"USD": 0.017, "EUR": 0.017}, drows), drows


ZERO5 = dict(instr_type="BOND", currency="USD", notional=1e6, rate=0.0, maturity_years=5.0)


def test_cvr_formula_identity_and_shock_size():
    (r,), drows = _cvr(ZERO5, 1.0)
    assert r["shock"] == 0.017
    assert r["s_sum"] == pytest.approx(sum(x["sensitivity"] for x in drows))
    assert r["cvr_up"] == pytest.approx(-(r["v_up"] - r["v_base"] - 0.017 * r["s_sum"]))
    assert r["cvr_dn"] == pytest.approx(-(r["v_dn"] - r["v_base"] + 0.017 * r["s_sum"]))


def test_long_convex_bond_has_no_curvature_charge_and_short_has():
    (lg,), _ = _cvr(ZERO5, 1.0)
    (sh,), _ = _cvr(ZERO5, -1.0)
    assert lg["cvr_up"] < 0 and lg["cvr_dn"] < 0                       # convexity helps a long: negative CVR
    assert sh["cvr_up"] == pytest.approx(-lg["cvr_up"]) and sh["cvr_dn"] == pytest.approx(-lg["cvr_dn"])
    # second-order check: CVR ~ -(1/2) T^2 h^2 V  (T=5, h=1.7%)
    v = 1e6 * math.exp(-0.2)
    assert lg["cvr_up"] == pytest.approx(-0.5 * 25 * 0.017**2 * v, rel=0.06)


def test_linear_fx_forward_exposure_has_tiny_curvature_relative_to_delta():
    inst = dict(instr_type="FXFWD", currency="EUR", notional=1e6, rate=1.1, maturity_years=0.25)
    rows, _ = _cvr(inst, 1.0)
    assert {r["bucket"] for r in rows} == {"USD", "EUR"}               # each curve shocked separately
    assert all(abs(r["cvr_up"]) < 0.01 * abs(r["s_sum"]) * 0.017 + 1.0 for r in rows)


def test_swaption_atm_price_and_parity():
    curve = np.full((1, 10), 0.04)
    n, e, tn, vol = 1e6, 2.0, 5, 0.009
    t = e + np.arange(1.0, tn + 0.5, 1.0)
    a = np.exp(-0.04 * t).sum()
    fwd = (np.exp(-0.04 * e) - np.exp(-0.04 * t[-1])) / a
    payer = pricing.pv_swaption(curve, n, fwd, e, tn, vol, True)[0]
    recv = pricing.pv_swaption(curve, n, fwd, e, tn, vol, False)[0]
    assert payer == pytest.approx(n * a * vol * math.sqrt(e) / math.sqrt(2 * math.pi), rel=1e-9)   # Bachelier ATM
    assert payer == pytest.approx(recv)
    k = fwd + 0.01
    parity = pricing.pv_swaption(curve, n, k, e, tn, vol, True)[0] - pricing.pv_swaption(curve, n, k, e, tn, vol, False)[0]
    assert parity == pytest.approx(n * a * (fwd - k), rel=1e-9)        # payer - receiver = N*A*(F-K)


def test_long_atm_swaption_is_long_gamma_short_is_curvature_charge():
    sw = dict(instr_type="SWAPTION", currency="USD", notional=1e8, rate=0.0457, maturity_years=2.0,
              underlying_tenor=5.0, normal_vol=0.009, option_type="PAYER")
    (lg,), _ = _cvr(sw, 1.0)
    (sh,), _ = _cvr(sw, -1.0)
    assert lg["cvr_up"] < 0 and lg["cvr_dn"] < 0
    assert sh["cvr_up"] > 0 and sh["cvr_dn"] > 0
    # the delta-only linearisation understates a short option's loss: charge is the shortfall
    assert sh["cvr_dn"] > sh["cvr_up"] * 0.2


def test_only_swaptions_are_flagged_as_optionality(built):
    rows = dict(built.execute("SELECT instr_type, MAX(has_optionality) FROM instruments GROUP BY instr_type"))
    assert rows == {"BOND": 0, "FXFWD": 0, "IRS": 0, "SWAPTION": 1}


# ------------------------------------------------------------------ whole portfolio
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    conn, _ = pipeline.build_database(tmp_path_factory.mktemp("cdb") / "c.db")
    for b in ("ALL", "TRADING"):
        sbm.run_basis(conn, b)
    return conn


@pytest.mark.parametrize("basis", ["ALL", "TRADING"])
@pytest.mark.parametrize("cmode", ["OPT", "ALL"])
def test_curvature_sql_matches_independent(built, basis, cmode):
    m = reconcile.reconcile_curvature(built, basis, cmode)
    assert len(m) > 0 and m["abs_diff"].max() < 1e-6


def test_curvature_cvrs_recompute_from_stored_values(built):
    import pandas as pd
    c = pd.read_sql("SELECT * FROM curvature_cvr", built)
    assert np.allclose(c.cvr_up, -(c.v_up - c.v_base - c["shock"] * c.s_sum), rtol=1e-9, atol=1e-6)
    assert np.allclose(c.cvr_dn, -(c.v_dn - c.v_base + c["shock"] * c.s_sum), rtol=1e-9, atol=1e-6)
    assert (c["shock"] == 0.017).all()                                  # highest GIRR delta RW (MAR21.99)


def test_firm_total_is_delta_plus_vega_plus_curvature_at_binding_scenario(built):
    comp = dict(built.execute("SELECT component, charge FROM res_sbm_components WHERE basis='ALL' AND scope='FIRM' "
                              "AND risk_class='TOTAL'"))
    tot = built.execute("SELECT charge_at_binding FROM res_sbm_final WHERE basis='ALL' AND scope='FIRM' "
                        "AND risk_class='TOTAL'").fetchone()[0]
    assert comp["delta"] + comp["vega"] + comp["curvature"] == pytest.approx(tot)
