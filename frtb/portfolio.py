"""Synthetic portfolio generator. EVERYTHING here is made up (seeded, reproducible).

4 desks, 9 books, ~170 positions: government bonds, receive/pay-fixed IRS, FX forwards,
in USD / EUR / GBP / CHF. One banking-book desk (Treasury ALM) and one book whose stated
intent conflicts with its holding period (to exercise the review flag).
"""
import numpy as np

from .config import SEED, TENORS

DESKS = [
    ("US_RATES", "US rates market making and hedging"),
    ("GLOBAL_RATES", "EUR/GBP/CHF govies and swaps"),
    ("FX_DESK", "G10 FX forwards"),
    ("TREASURY_ALM", "Banking-book ALM portfolio"),
]
# book_name, desk, purpose, accounting_class, expected_holding_days, n_positions, kind, ccys
BOOKS = [
    ("UST_MM",        "US_RATES",     "market_making",    "FVTPL_TRADING", 30,   28, "BOND", ["USD"]),
    ("USD_SWAP_HEDGE","US_RATES",     "hedge_of_trading", "FVTPL_TRADING", 60,   22, "IRS",  ["USD"]),
    ("UST_CARRY",     "US_RATES",     "short_term_resale","FVTPL_TRADING", 420,   8, "BOND", ["USD"]),
    ("EUR_GOVT_MM",   "GLOBAL_RATES", "market_making",    "FVTPL_TRADING", 30,   20, "BOND", ["EUR"]),
    ("GBP_GOVT_MM",   "GLOBAL_RATES", "market_making",    "FVTPL_TRADING", 30,   14, "BOND", ["GBP"]),
    ("CHF_GOVT_MM",   "GLOBAL_RATES", "market_making",    "FVTPL_TRADING", 45,    8, "BOND", ["CHF"]),
    ("G3_SWAP_ARB",   "GLOBAL_RATES", "arbitrage",        "FVTPL_TRADING", 90,   18, "IRS",  ["EUR", "GBP", "CHF"]),
    ("FX_FWD_G10",    "FX_DESK",      "market_making",    "FVTPL_TRADING", 20,   35, "FXFWD",["EUR", "GBP", "CHF"]),
    ("ALM_HTM",       "TREASURY_ALM", "hold_to_maturity", "AMORTISED_COST",1500, 20, "BOND", ["USD", "EUR", "CHF"]),
    ("USD_SWPTN_MM",  "US_RATES",     "market_making",    "FVTPL_TRADING", 45,   12, "SWAPTION", ["USD"]),
    ("EUR_SWPTN_MM",  "GLOBAL_RATES", "market_making",    "FVTPL_TRADING", 45,   10, "SWAPTION", ["EUR"]),
]
BOND_MATS = [2, 3, 5, 7, 10, 20, 30]
SWAP_MATS = [2, 3, 5, 7, 10, 15, 30]
FWD_MATS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
OPT_EXPIRIES = [0.5, 1.0, 2.0, 3.0, 5.0]
OPT_TENORS = [2, 5, 10]
NORMAL_VOL = 0.0090          # 90bp normal vol, ILLUSTRATIVE, flat, not calibrated to any market


def _zero(curve: dict, t: float) -> float:
    ts = np.array(TENORS)
    return float(np.interp(t, ts, [curve[x] for x in TENORS]))


def generate(curves: dict, fx: dict, asof: str):
    """Return (desks, books, instruments, positions) as lists of dict rows."""
    rng = np.random.default_rng(SEED)
    desks = [dict(desk_id=i + 1, desk_name=n, description=d) for i, (n, d) in enumerate(DESKS)]
    desk_id = {d["desk_name"]: d["desk_id"] for d in desks}
    books, instruments, positions = [], [], []
    iid = pid = 0
    for bi, (name, desk, purpose, acct, hold, n, kind, ccys) in enumerate(BOOKS, start=1):
        books.append(dict(book_id=bi, desk_id=desk_id[desk], book_name=name, stated_purpose=purpose,
                          accounting_class=acct, expected_holding_days=hold, legal_impediment=0))
        for _ in range(n):
            ccy = ccys[rng.integers(len(ccys))]
            iid += 1
            pid += 1
            if kind == "BOND":
                m = int(rng.choice(BOND_MATS))
                cpn = round(_zero(curves[ccy], m) + rng.normal(0, 0.003), 4)
                notional = float(rng.choice([5, 10, 15, 25, 40, 60])) * 1e6
                qty = 1.0 if rng.random() < 0.75 else -1.0
                inst = dict(instr_type="BOND", description=f"{ccy} govt {cpn*100:.2f}% {m}y", currency=ccy,
                            notional=notional, rate=max(cpn, 0.0), maturity_years=float(m))
            elif kind == "IRS":
                m = int(rng.choice(SWAP_MATS))
                fixed = round(_zero(curves[ccy], m) + rng.normal(0, 0.001), 4)
                notional = float(rng.choice([20, 40, 75, 100, 150])) * 1e6
                qty = 1.0 if rng.random() < 0.5 else -1.0
                inst = dict(instr_type="IRS", description=f"{ccy} IRS {fixed*100:.2f}% {m}y", currency=ccy,
                            notional=notional, rate=fixed, maturity_years=float(m))
            elif kind == "SWAPTION":
                expiry = float(rng.choice(OPT_EXPIRIES))
                tenor = int(rng.choice(OPT_TENORS))
                tt = expiry + np.arange(1.0, tenor + 0.5, 1.0)
                df = np.exp(-np.array([_zero(curves[ccy], x) for x in tt]) * tt)
                fwd = (np.exp(-_zero(curves[ccy], expiry) * expiry) - df[-1]) / df.sum()
                strike = round(float(fwd + rng.choice([-0.01, -0.005, 0.0, 0.005, 0.01])), 4)
                notional = float(rng.choice([50, 100, 200, 300])) * 1e6
                qty = 1.0 if rng.random() < 0.40 else -1.0   # market maker: short-gamma bias
                otype = "PAYER" if rng.random() < 0.5 else "RECEIVER"
                inst = dict(instr_type="SWAPTION", description=f"{ccy} {expiry:g}y x {tenor}y {otype.lower()} K={strike*100:.2f}%",
                            currency=ccy, notional=notional, rate=strike, maturity_years=expiry,
                            underlying_tenor=float(tenor), normal_vol=NORMAL_VOL, option_type=otype, has_optionality=1)
            else:
                m = float(rng.choice(FWD_MATS))
                s = fx[ccy]
                fair = s * np.exp(-_zero(curves[ccy], m) * m) / np.exp(-_zero(curves["USD"], m) * m)
                strike = round(float(fair * (1 + rng.normal(0, 0.002))), 5)
                notional = float(rng.choice([5, 10, 20, 30, 40])) * 1e6
                qty = 1.0 if rng.random() < 0.5 else -1.0
                inst = dict(instr_type="FXFWD", description=f"{ccy}USD fwd {m:g}y K={strike}", currency=ccy,
                            notional=notional, rate=strike, maturity_years=m)
            for k, dflt in (("underlying_tenor", None), ("normal_vol", None), ("option_type", None),
                            ("has_optionality", 0)):
                inst.setdefault(k, dflt)
            inst["instrument_id"] = iid
            instruments.append(inst)
            positions.append(dict(position_id=pid, book_id=bi, instrument_id=iid, quantity=qty,
                                  trade_date=asof))
    return desks, books, instruments, positions
