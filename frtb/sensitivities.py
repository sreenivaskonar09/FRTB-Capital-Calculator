"""Step 1: bump-and-revalue sensitivities in reporting currency (USD).

GIRR (MAR21.19): s = (V(r_t + 1bp) - V(r_t)) / 0.0001, per currency curve and tenor vertex.
FX   (MAR21.24): s = (V(1.01*FX) - V(FX)) / 0.01, per non-reporting currency.
Both are expressed in USD, i.e. FX translation of a foreign-currency PV uses the spot.
"""
import numpy as np

from .config import TENORS, VEGA_TENORS
from .pricing import swaption_vega, value_usd

BP = 1e-4


def _scen(curves, fx):
    return {c: np.array([v[t] if isinstance(v, dict) else v[i] for i, t in enumerate(TENORS)], float).reshape(1, -1)
            for c, v in curves.items()}, \
           {c: np.array([x], float) for c, x in fx.items()}


def position_sensitivities(instr: dict, qty: float, curves: dict, fx: dict) -> list[dict]:
    """Return rows: {risk_class, bucket, risk_factor, tenor, sensitivity, pv01_usd}."""
    c0, f0 = _scen(curves, fx)
    base = value_usd(instr, qty, c0, f0)[0]
    rows = []
    ccys = {instr["currency"], "USD"} if instr["instr_type"] == "FXFWD" else {instr["currency"]}
    for ccy in sorted(ccys):
        for i, t in enumerate(TENORS):
            cb = {k: v.copy() for k, v in c0.items()}
            cb[ccy][0, i] += BP
            s = (value_usd(instr, qty, cb, f0)[0] - base) / BP
            if abs(s) > 1e-6:
                rows.append(dict(risk_class="GIRR", bucket=ccy, risk_factor=f"{ccy}_GIRR_{t:g}Y",
                                 tenor=t, sensitivity=s, pv01_usd=s * BP))
    ccy = instr["currency"]
    if ccy != "USD":
        fb = {k: v.copy() for k, v in f0.items()}
        fb[ccy] = fb[ccy] * 1.01
        s = (value_usd(instr, qty, c0, fb)[0] - base) / 0.01
        if abs(s) > 1e-6:
            rows.append(dict(risk_class="FX", bucket=ccy, risk_factor=f"{ccy}USD",
                             tenor=None, sensitivity=s, pv01_usd=None))
    return rows


def position_curvature(instr: dict, qty: float, curves: dict, fx: dict, shocks: dict, delta_rows: list[dict]) -> list[dict]:
    """GIRR curvature inputs (MAR21.5(2), MAR21.99) per currency curve the position depends on.

    shocks: {ccy: parallel shift in decimal} (highest delta RW). s_sum: sum over all tenors of the
    position's GIRR delta sensitivities to that curve (MAR21.5(2)(f)(iii)).
        CVR_up = -(V(curve+RW) - V - RW*s)        CVR_dn = -(V(curve-RW) - V + RW*s)
    A position that depends on several curves gets one row per curve (each shocked on its own)."""
    c0, f0 = _scen(curves, fx)
    base = value_usd(instr, qty, c0, f0)[0]
    rows = []
    ccys = {instr["currency"], "USD"} if instr["instr_type"] == "FXFWD" else {instr["currency"]}
    for ccy in sorted(ccys):
        h = shocks[ccy]
        s_sum = sum(r["sensitivity"] for r in delta_rows if r["risk_class"] == "GIRR" and r["bucket"] == ccy)
        up = {k: v.copy() for k, v in c0.items()}
        dn = {k: v.copy() for k, v in c0.items()}
        up[ccy] += h
        dn[ccy] -= h
        v_up = value_usd(instr, qty, up, f0)[0]
        v_dn = value_usd(instr, qty, dn, f0)[0]
        rows.append(dict(bucket=ccy, risk_factor=f"{ccy}_GIRR_CURV", shock=h, s_sum=s_sum, v_base=base,
                         v_up=v_up, v_dn=v_dn,
                         cvr_up=-(v_up - base - h * s_sum), cvr_dn=-(v_dn - base + h * s_sum)))
    return rows


def grid_weights(x: float, grid=VEGA_TENORS) -> list[tuple[float, float]]:
    """Linear interpolation weights of x onto the vega tenor grid; flat outside (MAR21.8 fn3 style)."""
    if x <= grid[0]:
        return [(grid[0], 1.0)]
    if x >= grid[-1]:
        return [(grid[-1], 1.0)]
    for lo, hi in zip(grid[:-1], grid[1:]):
        if lo <= x <= hi:
            w = (x - lo) / (hi - lo)
            return [(t, wt) for t, wt in ((lo, 1 - w), (hi, w)) if wt > 0]
    raise ValueError(x)


def position_vega(instr: dict, qty: float, curves: dict, fx: dict) -> list[dict]:
    """GIRR vega sensitivities (MAR21.25): s = vega * implied vol, mapped onto the (option maturity x
    underlying maturity) grid with bilinear weights. Only instruments with optionality have vega."""
    if not instr.get("has_optionality"):
        return []
    c0, f0 = _scen(curves, fx)
    ccy = instr["currency"]
    vega = qty * f0[ccy][0] * swaption_vega(c0[ccy], instr["notional"], instr["rate"], instr["maturity_years"],
                                            instr["underlying_tenor"], instr["normal_vol"])[0]
    vol = instr["normal_vol"]
    rows = []
    for te, we in grid_weights(instr["maturity_years"]):
        for tu, wu in grid_weights(instr["underlying_tenor"]):
            w = we * wu
            rows.append(dict(bucket=ccy, risk_factor=f"{ccy}_GIRR_VEGA_{te:g}Y_{tu:g}Y", option_tenor=te,
                             underlying_tenor=tu, alloc_weight=w, vega=vega, implied_vol=vol,
                             sensitivity=w * vega * vol))
    return rows
