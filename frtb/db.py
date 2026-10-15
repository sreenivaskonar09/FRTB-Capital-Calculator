"""Database setup and loading (SQLite)."""
import csv
import math
import sqlite3
from pathlib import Path

import pandas as pd

from .config import CONFIG_DIR, DB_PATH, SQL_DIR, REPORTING_CCY, TENORS


def connect(path=DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)      # data/ is git-ignored, so a clone has none
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn) -> None:
    conn.executescript((SQL_DIR / "00_schema.sql").read_text(encoding="utf-8"))


def _rows(name):
    with open(CONFIG_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_parameters(conn) -> None:
    """Load every parameter table from config/*.csv (written by scripts/extract_params.py)."""
    conn.executemany("INSERT INTO sbm_parameters VALUES (?,?,?,?,?,?)",
                     [(r["param_name"], float(r["value"]), r["unit"], r["source_ref"],
                       r["verified_in_text"], r["note"]) for r in _rows("scalar_parameters.csv")])
    # derived GIRR vega risk weight, MAR21.92 fn24: min(RW_sigma * sqrt(LH)/sqrt(10), cap)
    pv = {r["param_name"]: float(r["value"]) for r in _rows("scalar_parameters.csv")}
    conn.execute("INSERT INTO sbm_parameters VALUES ('vega_rw_girr', ?, 'rw', 'MAR21.92 fn24', 'Y', "
                 "'derived: min(55% * sqrt(60)/sqrt(10), 100%)')",
                 (min(pv["vega_rw_sigma"] * math.sqrt(pv["vega_lh_girr_days"]) / math.sqrt(10), pv["vega_rw_cap"]),))
    # derived constant used by the sqrt(2) options of MAR21.44 / MAR21.88
    conn.execute("INSERT INTO sbm_parameters VALUES ('inv_sqrt2', ?, 'mult', 'MAR21.44/21.88', 'Y', "
                 "'1/sqrt(2), derived')", (1 / math.sqrt(2),))
    conn.executemany("INSERT INTO girr_tenor_rw VALUES (?,?,?)",
                     [(float(r["tenor_years"]), float(r["risk_weight"]), r["source_ref"])
                      for r in _rows("girr_tenor_rw.csv")])
    conn.executemany("INSERT INTO girr_tenor_corr VALUES (?,?,?,?)",
                     [(float(r["tenor_k"]), float(r["tenor_l"]), float(r["rho"]), r["source_ref"])
                      for r in _rows("girr_tenor_corr.csv")])
    conn.executemany("INSERT INTO specified_currencies VALUES (?,?,?,?)",
                     [(r["currency"], int(r["girr_specified"]), int(r["fx_pair_vs_usd_specified"]),
                       r["source_ref"]) for r in _rows("specified_currencies.csv")])
    conn.executemany("INSERT INTO girr_vega_tenors VALUES (?,?)",
                     [(float(r["tenor_years"]), r["source_ref"]) for r in _rows("girr_vega_tenors.csv")])
    conn.executemany("INSERT INTO liquidity_horizons VALUES (?,?,?)",
                     [(int(r["j"]), int(r["lh_days"]), r["source_ref"]) for r in _rows("liquidity_horizons.csv")])


def load_portfolio(conn, desks, books, instruments, positions, curves, curve_notes, fx, fx_note) -> None:
    conn.executemany("INSERT INTO desks VALUES (:desk_id,:desk_name,:description)", desks)
    conn.executemany(
        "INSERT INTO books (book_id,desk_id,book_name,stated_purpose,accounting_class,expected_holding_days,"
        "legal_impediment) VALUES (:book_id,:desk_id,:book_name,:stated_purpose,:accounting_class,"
        ":expected_holding_days,:legal_impediment)", books)
    conn.executemany(
        "INSERT INTO instruments (instrument_id,instr_type,description,currency,notional,rate,maturity_years,"
        "underlying_tenor,normal_vol,option_type,has_optionality) VALUES (:instrument_id,:instr_type,:description,"
        ":currency,:notional,:rate,:maturity_years,:underlying_tenor,:normal_vol,:option_type,:has_optionality)",
        instruments)
    conn.executemany("INSERT INTO positions VALUES (:position_id,:book_id,:instrument_id,:quantity,:trade_date)",
                     positions)
    conn.executemany("INSERT INTO curves VALUES (?,?,?,?)",
                     [(c, t, r, curve_notes[c]) for c, cv in curves.items() for t, r in cv.items()])
    conn.executemany("INSERT INTO fx_spots VALUES (?,?,?)", [(c, v, fx_note) for c, v in fx.items()])
    # risk factor reference data
    rf = []
    for c in curves:
        for t in TENORS:
            rf.append((f"{c}_GIRR_{t:g}Y", "GIRR", c, t))
    for c in fx:
        if c != REPORTING_CCY:
            rf.append((f"{c}USD", "FX", c, None))
    conn.executemany("INSERT INTO risk_factors VALUES (?,?,?,?)", rf)


def load_limits(conn) -> None:
    rows = _rows("limits.csv")
    desk_id = dict(conn.execute("SELECT desk_name, desk_id FROM desks").fetchall())
    conn.executemany("INSERT INTO limits VALUES (?,?,?)",
                     [(desk_id[r["desk_name"]], r["risk_class"], float(r["limit_amount"])) for r in rows])


def load_sensitivities(conn, rows) -> None:
    conn.executemany(
        "INSERT INTO sensitivities (position_id,risk_class,bucket,risk_factor,tenor_years,sensitivity,pv01_usd)"
        " VALUES (:position_id,:risk_class,:bucket,:risk_factor,:tenor,:sensitivity,:pv01_usd)", rows)


def load_curvature(conn, rows) -> None:
    conn.executemany(
        "INSERT INTO curvature_cvr VALUES (:position_id,:bucket,:risk_factor,:shock,:s_sum,:v_base,:v_up,:v_dn,"
        ":cvr_up,:cvr_dn)", rows)


def load_vega(conn, rows) -> None:
    conn.executemany(
        "INSERT INTO vega_sensitivities VALUES (:position_id,:bucket,:risk_factor,:option_tenor,:underlying_tenor,"
        ":alloc_weight,:vega,:implied_vol,:sensitivity)", rows)
