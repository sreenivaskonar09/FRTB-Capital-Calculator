"""Vectorised pricing of the three instrument types, in reporting currency (USD).

Curves are zero rates (continuous compounding) on the 10 GIRR vertices, linearly interpolated in
tenor with flat extrapolation. A curve argument has shape (S, 10): S scenarios (S=1 for base / bumps).
Simplifications (educational): single-curve discounting, annual fixed legs, no accrued interest,
no day counts. A swap's floating leg is valued as N*(1 - DF(T)).
"""
import numpy as np
from scipy.special import ndtr

from .config import TENORS

V = np.array(TENORS)


def interp_weights(times: np.ndarray) -> np.ndarray:
    """(len(times), 10) matrix W so that zero(times) = curve @ W.T (linear, flat extrapolation)."""
    W = np.zeros((len(times), len(V)))
    for k, t in enumerate(times):
        if t <= V[0]:
            W[k, 0] = 1.0
        elif t >= V[-1]:
            W[k, -1] = 1.0
        else:
            i = np.searchsorted(V, t) - 1
            w = (t - V[i]) / (V[i + 1] - V[i])
            W[k, i], W[k, i + 1] = 1 - w, w
    return W


def discount(curve: np.ndarray, times: np.ndarray) -> np.ndarray:
    """(S, len(times)) discount factors."""
    z = np.atleast_2d(curve) @ interp_weights(np.asarray(times, float)).T
    return np.exp(-z * np.asarray(times)[None, :])


def pv_bond(curve, notional, coupon, maturity):
    """Annual-coupon bond PV in local currency, shape (S,)."""
    times = np.arange(maturity, 0, -1.0)[::-1]
    cfs = np.full(len(times), notional * coupon)
    cfs[-1] += notional
    return discount(curve, times) @ cfs


def pv_irs_receive_fixed(curve, notional, fixed, maturity):
    """Receive-fixed swap PV in local currency: fixed leg - floating leg."""
    times = np.arange(1.0, maturity + 0.5, 1.0)
    df = discount(curve, times)
    fixed_leg = df @ np.full(len(times), notional * fixed)
    float_leg = notional * (1.0 - df[:, -1])
    return fixed_leg - float_leg


def pv_fxfwd_buy_foreign(curve_for, curve_usd, fx_spot, notional_for, strike, maturity):
    """Long foreign currency forward, PV in USD: S*N*DF_for - K*N*DF_usd."""
    df_f = discount(curve_for, [maturity])[:, 0]
    df_u = discount(curve_usd, [maturity])[:, 0]
    return notional_for * (fx_spot * df_f - strike * df_u)


def pv_swaption(curve, notional, strike, expiry, swap_tenor, normal_vol, payer: bool):
    """European swaption, Bachelier (normal) model, single-curve, annual fixed leg after expiry.
    V = N * A * [ w*(F-K)*Phi(w*d) + sigma*sqrt(T)*phi(d) ],  d = (F-K)/(sigma*sqrt(T)), w=+1 payer / -1 receiver.
    A = annuity, F = forward swap rate. Local-currency PV, shape (S,). Vol is ILLUSTRATIVE and held fixed."""
    pay_times = expiry + np.arange(1.0, swap_tenor + 0.5, 1.0)
    df_exp = discount(curve, [expiry])[:, 0]
    df_pay = discount(curve, pay_times)
    annuity = df_pay.sum(axis=1)
    fwd = (df_exp - df_pay[:, -1]) / annuity
    sd = normal_vol * np.sqrt(expiry)
    d = (fwd - strike) / sd
    w = 1.0 if payer else -1.0
    phi = np.exp(-0.5 * d * d) / np.sqrt(2 * np.pi)
    return notional * annuity * (w * (fwd - strike) * ndtr(w * d) + sd * phi)


def swaption_vega(curve, notional, strike, expiry, swap_tenor, normal_vol):
    """dV/d(sigma_normal) of the swaption above, local currency per 1.00 of vol, shape (S,).
    Same for payer and receiver: N * A * sqrt(T) * phi(d)."""
    pay_times = expiry + np.arange(1.0, swap_tenor + 0.5, 1.0)
    df_exp = discount(curve, [expiry])[:, 0]
    df_pay = discount(curve, pay_times)
    annuity = df_pay.sum(axis=1)
    fwd = (df_exp - df_pay[:, -1]) / annuity
    sd = normal_vol * np.sqrt(expiry)
    d = (fwd - strike) / sd
    return notional * annuity * np.sqrt(expiry) * np.exp(-0.5 * d * d) / np.sqrt(2 * np.pi)


def value_usd(instr: dict, quantity: float, curves: dict, fx: dict) -> np.ndarray:
    """Position value in USD for S scenarios.

    curves: {ccy: (S,10) array}; fx: {ccy: (S,) array USD per unit} (USD -> ones).
    """
    ccy, t = instr["currency"], instr["instr_type"]
    if t == "BOND":
        v = pv_bond(curves[ccy], instr["notional"], instr["rate"], instr["maturity_years"]) * fx[ccy]
    elif t == "IRS":
        v = pv_irs_receive_fixed(curves[ccy], instr["notional"], instr["rate"], instr["maturity_years"]) * fx[ccy]
    elif t == "SWAPTION":
        v = pv_swaption(curves[ccy], instr["notional"], instr["rate"], instr["maturity_years"],
                        instr["underlying_tenor"], instr["normal_vol"], instr["option_type"] == "PAYER") * fx[ccy]
    elif t == "FXFWD":
        v = pv_fxfwd_buy_foreign(curves[ccy], curves["USD"], fx[ccy], instr["notional"],
                                 instr["rate"], instr["maturity_years"])
    else:
        raise ValueError(t)
    return quantity * v
