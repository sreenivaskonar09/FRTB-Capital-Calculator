"""Hand-checkable SBM cases, run through the real SQL pipeline."""
import math

import pytest

from frtb import sbm

D = "DESK"


def test_single_position_girr(make_db, charges):
    # PV01 = -$1,000/bp at 5y USD -> s = -1,000/1e-4 = -1e7 ; RW(5y)=1.1% -> WS = -110,000
    sc, fin = charges(make_db([(D, 1, "GIRR", "USD", 5.0, -1e7)]))
    for scen in ("high", "medium", "low"):          # single factor: correlations irrelevant
        assert sc[(scen, "GIRR")] == pytest.approx(110_000.0)
    assert fin["TOTAL"][1] == pytest.approx(110_000.0)


def test_two_offsetting_positions_net_to_zero(make_db, charges):
    rows = [(D, 1, "GIRR", "USD", 5.0, -1e7), (D, 1, "GIRR", "USD", 5.0, +1e7),
            (D, 1, "FX", "EUR", None, 5e6), (D, 1, "FX", "EUR", None, -5e6)]
    sc, fin = charges(make_db(rows))
    assert fin["TOTAL"][1] == pytest.approx(0.0, abs=1e-9)


def test_single_bucket_two_tenors_all_scenarios(make_db, charges):
    # WS(5y) = 1.1% * 1e7 = 110,000 ; WS(10y) = 1.1% * 2e7 = 220,000 ; rho(5y,10y) = 97.0% (Table 2)
    a, b = 110_000.0, 220_000.0
    sc, _ = charges(make_db([(D, 1, "GIRR", "USD", 5.0, 1e7), (D, 1, "GIRR", "USD", 10.0, 2e7)]))
    kb = lambda r: math.sqrt(a * a + b * b + 2 * r * a * b)
    assert sc[("medium", "GIRR")] == pytest.approx(kb(0.97))
    assert sc[("high", "GIRR")] == pytest.approx(kb(1.0))                      # min(1.25*.97, 1) = 1
    assert sc[("low", "GIRR")] == pytest.approx(kb(max(2 * 0.97 - 1, 0.75 * 0.97)))  # 0.94


def test_two_buckets_gamma(make_db, charges):
    # USD 5y WS = 110,000 ; EUR 5y WS = -77,000 (s=-7e6) ; gamma 50% medium
    k1, k2 = 110_000.0, 77_000.0
    s1, s2 = 110_000.0, -77_000.0
    sc, _ = charges(make_db([(D, 1, "GIRR", "USD", 5.0, 1e7), (D, 1, "GIRR", "EUR", 5.0, -7e6)]))
    assert sc[("medium", "GIRR")] == pytest.approx(math.sqrt(k1**2 + k2**2 + 2 * 0.5 * s1 * s2))
    g_hi, g_lo = 0.625, max(2 * 0.5 - 1, 0.75 * 0.5)
    assert sc[("high", "GIRR")] == pytest.approx(math.sqrt(k1**2 + k2**2 + 2 * g_hi * s1 * s2))
    assert sc[("low", "GIRR")] == pytest.approx(math.sqrt(k1**2 + k2**2 + 2 * g_lo * s1 * s2))


def test_fx_two_buckets(make_db, charges):
    # EUR exposure +10m, GBP exposure -4m : WS = 15% * exposure ; gamma 60% (high: 75%, low: 45%)
    s1, s2 = 0.15 * 10e6, -0.15 * 4e6
    sc, _ = charges(make_db([(D, 1, "FX", "EUR", None, 10e6), (D, 1, "FX", "GBP", None, -4e6)]))
    f = lambda g: math.sqrt(s1**2 + s2**2 + 2 * g * s1 * s2)
    assert sc[("medium", "FX")] == pytest.approx(f(0.6))
    assert sc[("high", "FX")] == pytest.approx(f(0.75))
    assert sc[("low", "FX")] == pytest.approx(f(0.45))


def test_binding_scenario_is_the_max(make_db, charges):
    # two same-sign tenors -> more correlation = more capital -> 'high' binds
    sc, fin = charges(make_db([(D, 1, "GIRR", "USD", 2.0, 1e7), (D, 1, "GIRR", "USD", 30.0, 1e7)]))
    assert fin["TOTAL"][0] == "high"
    assert fin["TOTAL"][1] == pytest.approx(max(sc[(s, "GIRR")] for s in ("high", "medium", "low")))
    # opposite-sign tenors -> less correlation = more capital -> 'low' binds
    sc, fin = charges(make_db([(D, 1, "GIRR", "USD", 2.0, 1e7), (D, 1, "GIRR", "USD", 30.0, -1e7)]))
    assert fin["TOTAL"][0] == "low"


def test_sqrt2_option_off_and_on(make_db, charges):
    rows = [(D, 1, "FX", "EUR", None, 10e6)]
    _, off = charges(make_db(rows))
    _, on = charges(make_db(rows, sqrt2=True))
    assert off["TOTAL"][1] == pytest.approx(1.5e6)
    assert on["TOTAL"][1] == pytest.approx(1.5e6 / math.sqrt(2))


def test_alternative_sb_specification():
    # MAR21.4(5)(b): kb=1 each, sb=+2/-2, gamma .5 -> 1+1-4 < 0 -> clip S to +-Kb -> sqrt(1+1-1) = 1
    charge, alt, s = sbm.delta_charge({"a": 1.0, "b": 1.0}, {"a": 2.0, "b": -2.0}, 0.5)
    assert alt and s == {"a": 1.0, "b": -1.0} and charge == pytest.approx(1.0)
    charge, alt, _ = sbm.delta_charge({"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 1.0}, 0.5)
    assert not alt and charge == pytest.approx(math.sqrt(1 + 1 + 2 * 0.5))


def test_trading_basis_drops_banking_girr_keeps_fx(make_db, charges):
    rows = [("A", 1, "GIRR", "USD", 5.0, 1e7), ("B", 0, "GIRR", "USD", 5.0, 1e7), ("B", 0, "FX", "EUR", None, 1e7)]
    conn = make_db(rows)
    _, all_ = charges(conn, "ALL")
    _, trd = charges(conn, "TRADING")
    assert all_["GIRR"][1] == pytest.approx(220_000.0)     # both GIRR positions net: 2 * 110,000
    assert trd["GIRR"][1] == pytest.approx(110_000.0)      # banking-book GIRR excluded
    assert trd["FX"][1] == pytest.approx(all_["FX"][1])    # MAR12.6: banking-book FX stays in
