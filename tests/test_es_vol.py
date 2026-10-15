"""ES with volatility shocks: proxy construction, liquidity-horizon cascade, per-position vol P&L."""
import math

import numpy as np
import pandas as pd
import pytest

from frtb import es, marketdata, pipeline, pricing, sbm
from frtb.config import CURRENCIES, TENORS

LH = {1: 10, 2: 20, 3: 40, 4: 60, 5: 120}      # MAR33.4 Table 1
CURVES = {c: {t: 0.04 for t in TENORS} for c in CURRENCIES}
FX = {c: 1.0 if c == "USD" else 1.1 for c in CURRENCIES}


def zeros(S):
    return ({c: np.zeros((S, len(TENORS))) for c in CURRENCIES}, {c: np.zeros(S) for c in CURRENCIES if c != "USD"})


def atm_swaption():
    t = 1.0 + np.arange(1.0, 6.0)
    a = np.exp(-0.04 * t).sum()
    fwd = (np.exp(-0.04) - np.exp(-0.04 * t[-1])) / a
    return dict(instr_type="SWAPTION", currency="USD", notional=1e8, rate=fwd, maturity_years=1.0,
                underlying_tenor=5.0, normal_vol=0.009, option_type="PAYER", has_optionality=1)


# ------------------------------------------------------------------ vol proxy
def test_vol_proxy_ends_at_todays_vol_and_is_positive():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2020-01-01", periods=400, freq="B")
    rc = pd.DataFrame({("USD", 5.0): rng.normal(0, 0.0007, 400)}, index=idx)
    rc.columns = pd.MultiIndex.from_tuples(rc.columns)
    s = marketdata.normal_vol_proxy(rc, 0.009)
    assert s.iloc[-1] == pytest.approx(0.009) and (s > 0).all()
    assert len(s) == 400 - 59                                          # 60-day window


def test_vol_proxy_responds_to_a_volatility_jump():
    idx = pd.date_range("2020-01-01", periods=300, freq="B")
    sign = np.where(np.arange(300) % 2 == 0, 1.0, -1.0)               # +/- alternating: std = amplitude
    d = sign * np.r_[np.full(150, 0.0003), np.full(150, 0.0009)]      # rate vol triples
    rc = pd.DataFrame({("USD", 5.0): d}, index=idx)
    rc.columns = pd.MultiIndex.from_tuples(rc.columns)
    s = marketdata.normal_vol_proxy(rc, 0.009)
    assert s.iloc[-1] / s.iloc[80] == pytest.approx(3.0, rel=0.02)


# ------------------------------------------------------------------ liquidity horizon cascade
def test_cascade_reduces_to_es1_when_all_factors_are_10d():
    assert es.lh_cascade(5.0, {}, LH) == pytest.approx(5.0)


def test_cascade_20d_subset_has_unit_weight():
    # j=2: sqrt((20-10)/10) = 1  ->  sqrt(3^2 + 4^2) = 5
    assert es.lh_cascade(3.0, {2: 4.0}, LH) == pytest.approx(5.0)


def test_cascade_60d_vol_factors_equal_sqrt_time_scaling():
    # vol only: ES(P)=ES(P,2)=ES(P,3)=ES(P,4)=x  ->  x*sqrt(1+1+2+2) = x*sqrt(6) = x*sqrt(60/10)
    x = 7.0
    assert es.lh_cascade(x, {2: x, 3: x, 4: x}, LH) == pytest.approx(x * math.sqrt(60 / 10))


def test_cascade_j3_and_j4_each_add_two_ES_squared():
    # (ES_j * sqrt((40-20)/10))^2 = 2*ES^2 and (ES_j * sqrt((60-40)/10))^2 = 2*ES^2
    assert es.lh_cascade(3.0, {3: 1.0, 4: 1.0}, LH) == pytest.approx(math.sqrt(9 + 2 + 2))
    assert es.lh_cascade(3.0, {5: 100.0}, LH) == pytest.approx(math.sqrt(9 + (100 * math.sqrt(60 / 10)) ** 2))


def test_cascade_uses_the_cited_liquidity_horizon_table(make_db):
    conn = make_db([("D", 1, "FX", "EUR", None, 0.0)])
    assert dict(conn.execute("SELECT j, lh_days FROM liquidity_horizons")) == LH


# ------------------------------------------------------------------ position-level vol P&L
def test_atm_swaption_pnl_is_proportional_to_the_vol_shock():
    S = 3
    dR, dF = zeros(S)
    dM = np.array([1.1, 1.0, 0.9])
    p = es.position_pnls(atm_swaption(), 1.0, CURVES, FX, dR, dF, dM, S)
    price = pricing.pv_swaption(np.full((1, 10), 0.04), 1e8, atm_swaption()["rate"], 1.0, 5.0, 0.009, True)[0]
    assert p["V"] == pytest.approx(price * np.array([0.1, 0.0, -0.1]), rel=1e-9)   # ATM price is linear in sigma
    assert p["A1"] == pytest.approx(p["V"]) and p["RV"] == pytest.approx(p["V"])    # no rate / fx shock here
    assert np.allclose(p["A0"], 0) and np.allclose(p["R"], 0) and np.allclose(p["F"], 0)


def test_short_option_loses_when_vol_rises():
    S = 1
    dR, dF = zeros(S)
    long_ = es.position_pnls(atm_swaption(), 1.0, CURVES, FX, dR, dF, np.array([1.3]), S)["V"][0]
    short = es.position_pnls(atm_swaption(), -1.0, CURVES, FX, dR, dF, np.array([1.3]), S)["V"][0]
    assert long_ > 0 and short == pytest.approx(-long_)


@pytest.mark.parametrize("instr", [
    dict(instr_type="BOND", currency="USD", notional=1e6, rate=0.04, maturity_years=5.0, normal_vol=None),
    dict(instr_type="IRS", currency="EUR", notional=1e6, rate=0.04, maturity_years=5.0, normal_vol=None),
    dict(instr_type="FXFWD", currency="EUR", notional=1e6, rate=1.1, maturity_years=1.0, normal_vol=None)])
def test_linear_instruments_have_no_vol_pnl(instr):
    S = 2
    dR, dF = zeros(S)
    dR["USD"][:, 5] = [0.01, -0.01]
    p = es.position_pnls(instr, 1.0, CURVES, FX, dR, dF, np.array([1.5, 0.6]), S)
    assert np.allclose(p["V"], 0)
    assert np.allclose(p["A1"], p["A0"]) and np.allclose(p["RV"], p["R"])


def test_unit_vol_factors_reproduce_the_vol_fixed_measure():
    S = 4
    dR, dF = zeros(S)
    dR["USD"][:, 3] = [0.01, -0.01, 0.02, 0.0]
    p = es.position_pnls(atm_swaption(), 1.0, CURVES, FX, dR, dF, np.ones(S), S)
    assert np.allclose(p["A1"], p["A0"]) and np.allclose(p["V"], 0)


# ------------------------------------------------------------------ whole portfolio
@pytest.fixture(scope="module")
def es_rows(tmp_path_factory):
    conn, ctx = pipeline.build_database(tmp_path_factory.mktemp("esdb") / "e.db")
    for b in ("ALL", "TRADING"):
        sbm.run_basis(conn, b)
    rows = es.compute_es(conn, ctx)
    return {(s, m): v for s, m, v in rows}


def test_vol_only_es_is_the_60d_scaled_10d_es(es_rows):
    for scope in ("FIRM", "US_RATES"):
        assert es_rows[(scope, "ALL:VOL:es_stressed_lh_scaled")] == pytest.approx(
            es_rows[(scope, "ALL:VOL:es10_stressed")] * math.sqrt(6), rel=1e-9)


def test_books_without_options_are_unaffected_by_vol(es_rows):
    for scope in ("FX_DESK", "TREASURY_ALM"):
        assert es_rows[(scope, "ALL:ALL:es_stressed_lh_scaled")] == pytest.approx(
            es_rows[(scope, "ALL:ALL:es_stressed_lh_scaled_volfixed")], rel=1e-12)
        assert es_rows[(scope, "ALL:VOL:es10_stressed")] == pytest.approx(0.0, abs=1e-9)


def test_both_variants_are_produced_and_finite(es_rows):
    for basis in ("ALL", "TRADING"):
        for cls in ("ALL", "GIRR", "FX"):
            for m in ("es_stressed_lh_scaled", "es_stressed_lh_scaled_volfixed",
                      "es_current_lh_scaled", "es_current_lh_scaled_volfixed"):
                v = es_rows[("FIRM", f"{basis}:{cls}:{m}")]
                assert math.isfinite(v) and v >= 0


def test_fx_class_ignores_vol_and_stress_not_below_current(es_rows):
    assert es_rows[("FIRM", "ALL:FX:es_stressed_lh_scaled")] == pytest.approx(
        es_rows[("FIRM", "ALL:FX:es_stressed_lh_scaled_volfixed")], rel=1e-12)
    for variant in ("es_{}_lh_scaled", "es_{}_lh_scaled_volfixed"):
        assert es_rows[("FIRM", "ALL:ALL:" + variant.format("stressed"))] >= \
               es_rows[("FIRM", "ALL:ALL:" + variant.format("current"))] - 1e-6   # worst window >= last window
