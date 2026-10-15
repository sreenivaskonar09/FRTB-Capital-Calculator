"""Market data: latest curves / FX spots and the history used for the ES scenarios.

REAL (from FRED, downloaded to refs/fred): US Treasury CMT yields, EURUSD, GBPUSD, USDCHF.
ILLUSTRATIVE (made up): EUR, GBP, CHF zero curves and ALL non-USD rate *histories*.
  - FRED CMT yields are par yields; they are used directly as continuously compounded zero rates.
    That is a simplification, adequate for a sensitivity demo, wrong for pricing.
  - The 15y vertex is linearly interpolated between 10y and 20y (FRED has no 15y series).
"""
import numpy as np
import pandas as pd

from .config import FRED_DIR, TENORS, SEED

FRED_TENOR_COLS = {0.25: "DGS3MO", 0.5: "DGS6MO", 1.0: "DGS1", 2.0: "DGS2", 3.0: "DGS3",
                   5.0: "DGS5", 10.0: "DGS10", 20.0: "DGS20", 30.0: "DGS30"}

# Illustrative non-USD curves: (short-end level, long-end level) in decimal, shaped with a concave blend.
ILLUSTRATIVE = {"EUR": (0.0200, 0.0310), "GBP": (0.0400, 0.0470), "CHF": (0.0020, 0.0100)}
# Illustrative history model for non-USD rates: dr_ccy = beta * dr_USD + idio_bp * N(0,1)  (per day)
HIST_MODEL = {"EUR": (0.80, 0.0000050), "GBP": (0.90, 0.0000070), "CHF": (0.50, 0.0000040)}


def load_fred() -> pd.DataFrame:
    """Daily FRED panel since 2007: USD yields (decimal), EURUSD, GBPUSD, USDCHF (as USD per CHF)."""
    for f in ("fred_raw.csv", "fred_chf.csv"):
        if not (FRED_DIR / f).exists():
            raise FileNotFoundError(f"{FRED_DIR / f} not found - run:  python scripts/fetch_data.py")
    raw = pd.read_csv(FRED_DIR / "fred_raw.csv", parse_dates=["observation_date"], index_col=0)
    chf = pd.read_csv(FRED_DIR / "fred_chf.csv", parse_dates=["observation_date"], index_col=0)
    df = raw.join(chf, how="outer").loc["2007-01-01":]
    return df


def _interp_15y(y: dict) -> dict:
    y = dict(y)
    y[15.0] = y[10.0] + (y[20.0] - y[10.0]) * 0.5
    return {t: y[t] for t in TENORS}


def latest_curves_and_fx():
    """Return (asof_date, curves{ccy: {tenor: rate}}, fx{ccy: usd_per_unit}, source_notes)."""
    df = load_fred()
    ycols = list(FRED_TENOR_COLS.values())
    full = df.dropna(subset=ycols)
    asof = full.index[-1]
    row = full.loc[asof]
    usd = _interp_15y({t: row[c] / 100.0 for t, c in FRED_TENOR_COLS.items()})
    curves = {"USD": usd}
    notes = {"USD": f"FRED CMT par yields {asof.date()} (used as zero rates; 15y interpolated)"}
    t = np.array(TENORS)
    shape = 1 - np.exp(-t / 5.0)          # 0 at short end -> 1 at long end
    for ccy, (lo, hi) in ILLUSTRATIVE.items():
        curves[ccy] = {tt: float(lo + (hi - lo) * s) for tt, s in zip(TENORS, shape)}
        notes[ccy] = "ILLUSTRATIVE (not market data)"
    last = df.ffill().loc[asof]
    fx = {"USD": 1.0, "EUR": float(last["DEXUSEU"]), "GBP": float(last["DEXUSUK"]),
          "CHF": float(1.0 / last["DEXSZUS"])}
    notes.update({"fx": f"FRED DEXUSEU/DEXUSUK/DEXSZUS as of {asof.date()} (ffill)"})
    return asof, curves, fx, notes


def risk_factor_history(ccys) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    """Daily history for ES.

    Returns (rate_changes, fx_logret, dates).
      rate_changes: DataFrame columns MultiIndex (ccy, tenor), daily change in decimal rate.
      fx_logret:    DataFrame columns ccy (non-USD), daily log return of USD per unit.
    USD changes are REAL; non-USD rate changes are MODELLED (beta * USD + noise) - see HIST_MODEL.
    """
    df = load_fred()
    ycols = list(FRED_TENOR_COLS.values())
    y = (df[ycols].ffill().dropna() / 100.0)
    y.columns = list(FRED_TENOR_COLS.keys())
    y[15.0] = y[10.0] + (y[20.0] - y[10.0]) * 0.5
    y = y[TENORS]
    fx = df[["DEXUSEU", "DEXUSUK", "DEXSZUS"]].ffill().reindex(y.index).ffill().dropna()
    y = y.loc[fx.index]
    px = pd.DataFrame({"EUR": fx["DEXUSEU"], "GBP": fx["DEXUSUK"], "CHF": 1.0 / fx["DEXSZUS"]})
    dy = y.diff().dropna()
    fxr = np.log(px).diff().dropna().loc[dy.index]

    rng = np.random.default_rng(SEED)
    cols = {}
    for t in TENORS:
        cols[("USD", t)] = dy[t].values
    for ccy in ccys:
        if ccy == "USD":
            continue
        beta, idio = HIST_MODEL[ccy]
        for t in TENORS:
            cols[(ccy, t)] = beta * dy[t].values + idio * rng.standard_normal(len(dy))
    rc = pd.DataFrame(cols, index=dy.index)
    rc.columns = pd.MultiIndex.from_tuples(rc.columns)
    return rc, fxr, dy.index


def normal_vol_proxy(rc: pd.DataFrame, sigma_now: float, window: int = 60) -> pd.Series:
    """MODELLED proxy for the implied normal vol history (no free implied-vol data exists).

    60-day realised normal vol of the real USD 5y yield (daily changes, annualised), rescaled so that the
    latest value equals sigma_now (today's 90bp). Used as one flat relative shock for every swaption."""
    rv = rc[("USD", 5.0)].rolling(window).std() * np.sqrt(252)
    rv = rv.dropna()
    return sigma_now * rv / rv.iloc[-1]
