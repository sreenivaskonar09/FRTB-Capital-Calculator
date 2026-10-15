"""GIRR vega: hand-checkable aggregation (SQL path), sensitivity definition, tenor mapping, reconciliation."""
import math

import numpy as np
import pytest

from frtb import pipeline, pricing, reconcile, sbm
from frtb.config import TENORS, VEGA_TENORS
from frtb.sensitivities import grid_weights, position_vega

D = "DESK"
FLAT = {c: {t: 0.04 for t in TENORS} for c in ("USD", "EUR")}
FX = {"USD": 1.0, "EUR": 1.1}


def vega_db(make_db, rows):
    """rows: (bucket, option_tenor, underlying_tenor, sensitivity); one position each, one trading book."""
    conn = make_db([(D, 1, "FX", "EUR", None, 0.0)])
    book = conn.execute("SELECT book_id FROM books").fetchone()[0]
    for n, (b, te, tu, s) in enumerate(rows, 100):
        conn.execute("INSERT INTO positions VALUES (?,?,1,1.0,'2026-01-01')", (n, book))
        conn.execute("INSERT INTO vega_sensitivities VALUES (?,?,?,?,?,?,?,?,?)",
                     (n, b, f"{b}_GIRR_VEGA_{te:g}Y_{tu:g}Y", te, tu, 1.0, 0.0, 0.009, s))
    conn.commit()
    return conn


def vega_charges(conn, scope="FIRM"):
    sbm.run_basis(conn, "ALL")
    return {s: c for s, c in conn.execute(
        "SELECT scenario, vega_charge FROM res_vega_scenario WHERE basis='ALL' AND scope=?", (scope,))}


def rho(te1, tu1, te2, tu2, a=0.01):
    return min(math.exp(-a * abs(te1 - te2) / min(te1, te2)) * math.exp(-a * abs(tu1 - tu2) / min(tu1, tu2)), 1.0)


def test_risk_weight_is_the_100pct_cap(make_db):
    conn = make_db([(D, 1, "FX", "EUR", None, 0.0)])
    assert 0.55 * math.sqrt(60) / math.sqrt(10) > 1.0                    # 134.7% before the cap
    assert conn.execute("SELECT value FROM sbm_parameters WHERE param_name='vega_rw_girr'").fetchone()[0] == 1.0
    assert [r[0] for r in conn.execute("SELECT tenor_years FROM girr_vega_tenors ORDER BY 1")] == VEGA_TENORS


def test_single_factor_charge_is_abs_weighted_vega(make_db):
    ch = vega_charges(vega_db(make_db, [("USD", 1.0, 5.0, 1e6)]))
    assert ch == pytest.approx({"high": 1e6, "medium": 1e6, "low": 1e6})   # RW 100%, one factor


def test_short_vega_is_charged_like_long_vega(make_db):
    ch = vega_charges(vega_db(make_db, [("USD", 1.0, 5.0, -1e6)]))
    assert ch["medium"] == pytest.approx(1e6)


def test_offsetting_vega_nets_to_zero(make_db):
    ch = vega_charges(vega_db(make_db, [("USD", 1.0, 5.0, 1e6), ("USD", 1.0, 5.0, -1e6)]))
    assert ch == pytest.approx({"high": 0.0, "medium": 0.0, "low": 0.0}, abs=1e-9)


def test_two_option_maturities_same_bucket(make_db):
    a, b = 1e6, 2e6
    r = rho(1, 5, 3, 5)                                                  # exp(-1% * 2/1) = 0.9802
    ch = vega_charges(vega_db(make_db, [("USD", 1.0, 5.0, a), ("USD", 3.0, 5.0, b)]))
    k = lambda x: math.sqrt(a * a + b * b + 2 * x * a * b)
    assert ch["medium"] == pytest.approx(k(r))
    assert ch["high"] == pytest.approx(k(1.0))                           # min(1.25 * 0.98, 1) = 1
    assert ch["low"] == pytest.approx(k(max(2 * r - 1, 0.75 * r)))       # 0.9604


def test_correlation_multiplies_both_dimensions(make_db):
    a, b = 1e6, 1e6
    r = rho(0.5, 1, 10, 10)                                              # exp(-0.19) * exp(-0.09)
    assert r == pytest.approx(math.exp(-0.19) * math.exp(-0.09))
    ch = vega_charges(vega_db(make_db, [("USD", 0.5, 1.0, a), ("USD", 10.0, 10.0, b)]))
    assert ch["medium"] == pytest.approx(math.sqrt(a * a + b * b + 2 * r * a * b))
    assert ch["low"] == pytest.approx(math.sqrt(a * a + b * b + 2 * max(2 * r - 1, 0.75 * r) * a * b))


def test_two_currencies_gamma(make_db):
    s1, s2 = 1e6, -0.6e6
    ch = vega_charges(vega_db(make_db, [("USD", 1.0, 5.0, s1), ("EUR", 1.0, 5.0, s2)]))
    f = lambda g: math.sqrt(s1**2 + s2**2 + 2 * g * s1 * s2)
    assert ch["medium"] == pytest.approx(f(0.5))                         # vega gamma = delta gamma (MAR21.95)
    assert ch["high"] == pytest.approx(f(0.625))
    assert ch["low"] == pytest.approx(f(0.375))


def test_vega_corr_matrix_properties():
    p = dict(vega_alpha=0.01, scen_high_mult=1.25, scen_high_cap=1.0, scen_low_a=2.0, scen_low_b=0.75)
    f = [(0.5, 1.0), (1.0, 3.0), (10.0, 10.0)]
    for scen in ("high", "medium", "low"):
        R = sbm.vega_corr_matrix(f, scen, p)
        assert np.allclose(np.diag(R), 1.0) and np.allclose(R, R.T)
        assert (R <= 1.0 + 1e-12).all() and (R > 0).all()
    assert (sbm.vega_corr_matrix(f, "low", p) <= sbm.vega_corr_matrix(f, "medium", p) + 1e-12).all()
    assert (sbm.vega_corr_matrix(f, "high", p) >= sbm.vega_corr_matrix(f, "medium", p) - 1e-12).all()


# ------------------------------------------------------------------ definitions on real pricing code
def swpt(expiry, tenor, strike=None, option_type="PAYER", notional=1e8):
    t = expiry + np.arange(1.0, tenor + 0.5, 1.0)
    a = np.exp(-0.04 * t).sum()
    fwd = (np.exp(-0.04 * expiry) - np.exp(-0.04 * t[-1])) / a
    return dict(instr_type="SWAPTION", currency="USD", notional=notional, rate=fwd if strike is None else strike,
                maturity_years=expiry, underlying_tenor=float(tenor), normal_vol=0.009, option_type=option_type,
                has_optionality=1)


def test_atm_vega_sensitivity_equals_option_price():
    # Bachelier ATM: V = N*A*sigma*sqrt(T)/sqrt(2pi) and vega = N*A*sqrt(T)/sqrt(2pi)  =>  sigma*vega = V
    inst = swpt(1.0, 5)
    curve = np.full((1, 10), 0.04)
    price = pricing.pv_swaption(curve, inst["notional"], inst["rate"], 1.0, 5.0, 0.009, True)[0]
    (r,) = position_vega(inst, 1.0, FLAT, FX)
    assert (r["option_tenor"], r["underlying_tenor"], r["alloc_weight"]) == (1.0, 5.0, 1.0)
    assert r["sensitivity"] == pytest.approx(price, rel=1e-9)


def test_analytic_vega_matches_finite_difference():
    inst = swpt(2.0, 5, strike=0.05)
    curve = np.full((1, 10), 0.04)
    h = 1e-6
    up = pricing.pv_swaption(curve, 1e8, 0.05, 2.0, 5.0, 0.009 + h, True)[0]
    dn = pricing.pv_swaption(curve, 1e8, 0.05, 2.0, 5.0, 0.009 - h, True)[0]
    an = pricing.swaption_vega(curve, 1e8, 0.05, 2.0, 5.0, 0.009)[0]
    assert an == pytest.approx((up - dn) / (2 * h), rel=1e-6)


def test_payer_and_receiver_have_same_vega_and_direction_sets_sign():
    pay = position_vega(swpt(1.0, 5, 0.05, "PAYER"), 1.0, FLAT, FX)[0]["sensitivity"]
    rec = position_vega(swpt(1.0, 5, 0.05, "RECEIVER"), 1.0, FLAT, FX)[0]["sensitivity"]
    short = position_vega(swpt(1.0, 5, 0.05, "PAYER"), -1.0, FLAT, FX)[0]["sensitivity"]
    assert pay == pytest.approx(rec) and pay > 0 and short == pytest.approx(-pay)


def test_foreign_currency_vega_is_translated_to_usd():
    inst = dict(swpt(1.0, 5, 0.05), currency="EUR")
    usd = position_vega(dict(inst, currency="USD"), 1.0, FLAT, FX)[0]["sensitivity"]
    eur = position_vega(inst, 1.0, FLAT, FX)[0]["sensitivity"]
    assert eur == pytest.approx(usd * 1.1)


def test_tenor_mapping_bilinear_and_weights_conserve_vega():
    rows = position_vega(swpt(2.0, 2, 0.05), 1.0, FLAT, FX)             # 2y sits halfway between 1y and 3y
    assert len(rows) == 4 and all(r["alloc_weight"] == pytest.approx(0.25) for r in rows)
    assert {(r["option_tenor"], r["underlying_tenor"]) for r in rows} == {(1.0, 1.0), (1.0, 3.0), (3.0, 1.0), (3.0, 3.0)}
    assert sum(r["sensitivity"] for r in rows) == pytest.approx(rows[0]["vega"] * 0.009)
    assert grid_weights(0.25) == [(0.5, 1.0)] and grid_weights(15.0) == [(10.0, 1.0)]
    assert grid_weights(5.0) == [(5.0, 1.0)]


def test_linear_instruments_have_no_vega():
    bond = dict(instr_type="BOND", currency="USD", notional=1e6, rate=0.04, maturity_years=5.0)
    assert position_vega(bond, 1.0, FLAT, FX) == []


# ------------------------------------------------------------------ whole portfolio
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    conn, _ = pipeline.build_database(tmp_path_factory.mktemp("vdb") / "v.db")
    for b in ("ALL", "TRADING"):
        sbm.run_basis(conn, b)
    return conn


@pytest.mark.parametrize("basis", ["ALL", "TRADING"])
def test_vega_sql_matches_independent(built, basis):
    m = reconcile.reconcile_vega(built, basis)
    assert len(m) > 0 and m["abs_diff"].max() < 1e-6


def test_vega_rows_only_for_options_and_rebuild_from_raw(built):
    import pandas as pd
    types = {r[0] for r in built.execute("SELECT DISTINCT i.instr_type FROM vega_sensitivities v "
                                         "JOIN positions p USING (position_id) JOIN instruments i USING (instrument_id)")}
    assert types == {"SWAPTION"}
    v = pd.read_sql("SELECT * FROM vega_sensitivities", built)
    assert np.allclose(v.sensitivity, v.alloc_weight * v.vega * v.implied_vol, rtol=1e-12)
    per_pos = v.groupby("position_id")["alloc_weight"].sum()
    assert np.allclose(per_pos, 1.0)                                    # allocation conserves the total vega


def test_firm_vega_not_above_sum_of_desks_and_total_adds_up(built):
    q = "SELECT charge FROM res_sbm_components WHERE basis='ALL' AND scope=? AND risk_class='TOTAL' AND component='vega'"
    firm = built.execute(q, ("FIRM",)).fetchone()[0]
    desks = sum(built.execute(q, (s,)).fetchone()[0] for s in ("US_RATES", "GLOBAL_RATES"))
    assert firm <= desks + 1e-6
    comp = dict(built.execute("SELECT component, charge FROM res_sbm_components WHERE basis='ALL' AND scope='FIRM' "
                              "AND risk_class='TOTAL'"))
    tot = built.execute("SELECT charge_at_binding FROM res_sbm_final WHERE basis='ALL' AND scope='FIRM' "
                        "AND risk_class='TOTAL'").fetchone()[0]
    assert comp["delta"] + comp["vega"] + comp["curvature"] == pytest.approx(tot)
