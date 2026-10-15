"""IMA-style 97.5% Expected Shortfall by historical simulation (MAR33), partial and educational.

What it does
  * 10-day overlapping risk-factor changes from FRED history 2007-> (MAR33.4(7)); full revaluation.
  * Three shocked factor groups: interest rates, FX, and interest-rate VOLATILITY (swaption normal vol).
  * Liquidity-horizon cascade (MAR33.4): ES = sqrt( ES_T(P)^2 + sum_{j>=2} (ES_T(P,j) * sqrt((LH_j-LH_{j-1})/T))^2 ),
    with ES_T(P,j) shocking only the factors whose horizon is >= LH_j (MAR33.12 Table 2):
        rates, specified ccys 10d | CHF rates (unspecified ccy) 20d | FX specified pairs 10d | rate volatility 60d.
    So j=2 (20d) shocks CHF rates + vol, j=3 (40d) and j=4 (60d) shock vol only, j=5 (120d) nothing.
  * Stress period: the rolling 250-observation window with the largest ES (MAR33.7 idea), no reduced-set
    scaling because the 'reduced' and 'full' factor sets coincide in this toy.
  * A 'volfixed' variant (no vol shocks, the earlier version of this module) is kept for comparison.
VOL IS A MODELLED PROXY: no free implied-vol history exists, so the vol shock is the 10-day relative change in
a 60-day realised normal vol of the real USD 5y yield, scaled to today's 90bp and applied as one flat relative
shock to every swaption (no smile, no expiry/tenor structure, sticky strike). It is an assumption, not data.
What it does NOT do: IMCC aggregation across risk-class buckets with the rho = 0.5 blend (MAR33.15),
NMRF / stress scenario capital (incl. the risk factor eligibility test), default risk charge, backtesting or
PLA multipliers (MAR32/MAR33.42). Non-USD interest-rate histories are MODELLED (marketdata.HIST_MODEL).
"""
import math

import numpy as np
import pandas as pd

from . import marketdata
from .config import CURRENCIES, TENORS
from .portfolio import NORMAL_VOL
from .pricing import value_usd

H = 10          # base horizon, days (MAR33.4(2))
WINDOW = 250    # 12 months of observations
Q = 0.975

# (ES1 key, key for banking-book positions under TRADING basis, {j: key of the LH>=LH_j subset})
SPECS = {
    "vol":   {"ALL": ("A1", "F", {2: "C2", 3: "V", 4: "V"}),
              "GIRR": ("RV", None, {2: "C2", 3: "V", 4: "V"}),
              "FX": ("F", "F", {}),
              # diagnostic only (no SA counterpart): vol risk on its own, 60-day horizon via the cascade
              "VOL": ("V", None, {2: "V", 3: "V", 4: "V"})},
    "fixed": {"ALL": ("A0", "F", {2: "C"}),
              "GIRR": ("R", None, {2: "C"}),
              "FX": ("F", "F", {})},
}


def expected_shortfall(loss: np.ndarray, q: float = Q) -> float:
    """Mean of the worst ceil((1-q)*N) losses (losses positive)."""
    n = len(loss)
    k = max(1, math.ceil(round((1 - q) * n, 9)))
    return float(np.sort(loss)[-k:].mean())


def worst_window(loss: np.ndarray, w: int = WINDOW):
    """Start index of the w-observation window with the largest ES."""
    best, best_i = -np.inf, 0
    for i in range(0, len(loss) - w + 1):
        e = expected_shortfall(loss[i:i + w])
        if e > best:
            best, best_i = e, i
    return best_i, best


def lh_cascade(es1: float, es_j: dict, lh: dict, t: int = H) -> float:
    """MAR33.4: sqrt( ES1^2 + sum_{j>=2} (ES_j * sqrt((LH_j - LH_{j-1})/T))^2 ).
    es_j: {j: ES of the subset of factors with LH >= LH_j}; missing j means no such factors (0)."""
    tot = max(es1, 0.0) ** 2
    for j in sorted(lh):
        if j >= 2:
            tot += (max(es_j.get(j, 0.0), 0.0) * math.sqrt((lh[j] - lh[j - 1]) / t)) ** 2
    return math.sqrt(tot)


def build_scenarios():
    """10-day overlapping shocks: dR[ccy] (S,10) decimal rate changes, dF[ccy] (S,) log fx returns,
    dM (S,) multiplicative vol factors (modelled proxy). All on the same dates."""
    rc, fxr, dates = marketdata.risk_factor_history(CURRENCIES)
    r10 = rc.rolling(H).sum().dropna()
    f10 = fxr.rolling(H).sum().dropna()
    sig = marketdata.normal_vol_proxy(rc, NORMAL_VOL)
    m = (sig / sig.shift(H)).dropna()                      # 10-day relative change of the vol proxy
    idx = r10.index.intersection(f10.index).intersection(m.index)
    dR = {c: r10.loc[idx, c][TENORS].values for c in CURRENCIES}
    dF = {c: f10.loc[idx, c].values for c in CURRENCIES if c != "USD"}
    return idx, dR, dF, m.loc[idx].values


def position_pnls(instr, qty, base_curves, base_fx, dR, dF, dM, S):
    """P&L vectors (S,) for the shock subsets of one position (all other factors held at today's level):
    A0 rates+fx, A1 rates+fx+vol, R rates, RV rates+vol, F fx, V vol, C CHF rates, C2 CHF rates+vol."""
    curves0 = {c: np.tile(np.array([base_curves[c][t] for t in TENORS]), (S, 1)) for c in CURRENCIES}
    fx0 = {c: np.full(S, base_fx[c]) for c in CURRENCIES}
    v0 = value_usd(instr, qty, {c: v[:1] for c, v in curves0.items()}, {c: v[:1] for c, v in fx0.items()})[0]

    def shocked(rate_ccys, fx_ccys, vol):
        cv = {c: (curves0[c] + dR[c] if c in rate_ccys else curves0[c]) for c in CURRENCIES}
        fx = {c: (fx0[c] * np.exp(dF[c]) if c in fx_ccys else fx0[c]) for c in CURRENCIES}
        ins = dict(instr, normal_vol=instr["normal_vol"] * dM) if (vol and instr["instr_type"] == "SWAPTION") else instr
        return value_usd(ins, qty, cv, fx) - v0

    allc = set(CURRENCIES)
    nonusd = {c for c in CURRENCIES if c != "USD"}
    return dict(A0=shocked(allc, nonusd, False), A1=shocked(allc, nonusd, True),
                R=shocked(allc, set(), False), RV=shocked(allc, set(), True),
                F=shocked(set(), nonusd, False), V=shocked(set(), set(), True),
                C=shocked({"CHF"}, set(), False), C2=shocked({"CHF"}, set(), True))


def compute_es(conn, ctx) -> list[tuple]:
    """Return rows (scope, measure, value) for scopes FIRM + desks, bases ALL/TRADING, classes ALL/GIRR/FX.
    Measures without suffix include vol shocks; '*_volfixed' reproduce the earlier vol-held-fixed ES."""
    idx, dR, dF, dM = build_scenarios()
    S = len(idx)
    lh = dict(conn.execute("SELECT j, lh_days FROM liquidity_horizons"))
    inst = {i["instrument_id"]: i for i in ctx["instruments"]}
    meta = pd.read_sql(
        "SELECT p.position_id, p.instrument_id, p.quantity, d.desk_name, b.trading_book_flag "
        "FROM positions p JOIN books b ON b.book_id = p.book_id JOIN desks d ON d.desk_id = b.desk_id", conn)
    pnl = {r.position_id: position_pnls(inst[r.instrument_id], r.quantity, ctx["curves"], ctx["fx"], dR, dF, dM, S)
           for r in meta.itertuples()}

    rows = []
    scopes = ["FIRM"] + sorted(meta["desk_name"].unique())
    for basis in ("ALL", "TRADING"):
        for scope in scopes:
            m = meta if scope == "FIRM" else meta[meta["desk_name"] == scope]

            def total(key, bank_key):
                """Sum of position P&Ls for a subset key; banking-book positions use bank_key under TRADING
                (None = excluded; banking-book FX stays in, MAR12.6)."""
                acc = np.zeros(S)
                for r in m.itertuples():
                    k = bank_key if (basis == "TRADING" and r.trading_book_flag == 0) else key
                    if k is not None:
                        acc += pnl[r.position_id][k]
                return acc

            for variant, specs in SPECS.items():
                for cls, (k1, kbank, subsets) in specs.items():
                    loss1 = -total(k1, kbank)
                    loss_j = {j: -total(kj, None) for j, kj in subsets.items()}
                    i0, es_stress1 = worst_window(loss1)
                    es_cur1 = expected_shortfall(loss1[-WINDOW:])
                    stressed = lh_cascade(es_stress1, {j: expected_shortfall(l[i0:i0 + WINDOW])
                                                       for j, l in loss_j.items()}, lh)
                    current = lh_cascade(es_cur1, {j: expected_shortfall(l[-WINDOW:]) for j, l in loss_j.items()}, lh)
                    key = f"{basis}:{cls}"
                    if variant == "vol":
                        rows += [(scope, f"{key}:es10_current", max(es_cur1, 0)),
                                 (scope, f"{key}:es10_stressed", max(es_stress1, 0)),
                                 (scope, f"{key}:es_current_lh_scaled", current),
                                 (scope, f"{key}:es_stressed_lh_scaled", stressed),
                                 (scope, f"{key}:stress_window_start_idx", float(i0))]
                    else:
                        rows += [(scope, f"{key}:es_current_lh_scaled_volfixed", current),
                                 (scope, f"{key}:es_stressed_lh_scaled_volfixed", stressed)]
                    if variant == "vol" and basis == "ALL" and scope == "FIRM" and cls == "ALL":
                        rows += [("FIRM", "meta:stress_start", float(idx[i0].value)),
                                 ("FIRM", "meta:stress_end", float(idx[i0 + WINDOW - 1].value)),
                                 ("FIRM", "meta:n_scenarios", float(S)),
                                 ("FIRM", "meta:vol_factor_min", float(dM.min())),
                                 ("FIRM", "meta:vol_factor_max", float(dM.max()))]
    return rows
