import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frtb import db, sbm  # noqa: E402


@pytest.fixture
def make_db():
    """Factory: tiny in-memory DB with the real parameters and hand-set sensitivities.
    rows: (desk, trading_flag, risk_class, bucket, tenor_or_None, sensitivity)."""
    def _make(rows, sqrt2=False):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        db.create_schema(conn)
        db.load_parameters(conn)
        if sqrt2:
            conn.execute("UPDATE sbm_parameters SET value = 1 WHERE param_name IN "
                         "('girr_rw_sqrt2_option','fx_rw_sqrt2_option')")
        desks = sorted({r[0] for r in rows})
        for i, d in enumerate(desks, 1):
            conn.execute("INSERT INTO desks VALUES (?,?,?)", (i, d, ""))
        for i, (d, flag) in enumerate(sorted({(r[0], r[1]) for r in rows}), 1):
            conn.execute("INSERT INTO books (book_id,desk_id,book_name,stated_purpose,accounting_class,"
                         "expected_holding_days,trading_book_flag) VALUES (?,?,?,?,?,?,?)",
                         (i, desks.index(d) + 1, f"{d}_{flag}", "market_making", "FVTPL_TRADING", 10, flag))
        conn.execute("INSERT INTO instruments (instrument_id,instr_type,description,currency,notional,rate,"
                     "maturity_years) VALUES (1,'BOND','x','USD',1,0,1)")
        for n, (d, flag, rc, bucket, tenor, s) in enumerate(rows, 1):
            bid = conn.execute("SELECT book_id FROM books WHERE book_name=?", (f"{d}_{flag}",)).fetchone()[0]
            rf = f"{bucket}_GIRR_{tenor:g}Y" if rc == "GIRR" else f"{bucket}USD"
            conn.execute("INSERT OR IGNORE INTO risk_factors VALUES (?,?,?,?)", (rf, rc, bucket, tenor))
            conn.execute("INSERT INTO positions VALUES (?,?,?,?,?)", (n, bid, 1, 1.0, "2026-01-01"))
            conn.execute("INSERT INTO sensitivities (position_id,risk_class,bucket,risk_factor,tenor_years,"
                         "sensitivity) VALUES (?,?,?,?,?,?)", (n, rc, bucket, rf, tenor, s))
        conn.commit()
        return conn
    return _make


@pytest.fixture
def charges():
    def _get(conn, basis="ALL", scope="FIRM"):
        sbm.run_basis(conn, basis)
        sc = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT scenario, risk_class, delta_charge FROM res_delta_scenario WHERE basis=? AND scope=?",
            (basis, scope))}
        fin = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT risk_class, binding_scenario, charge_at_binding FROM res_sbm_final WHERE basis=? AND scope=?",
            (basis, scope))}
        return sc, fin
    return _get


def pytest_collection_modifyitems(config, items):
    """Tests that build the full portfolio need the FRED files; skip them with a clear message if absent."""
    from frtb.config import FRED_DIR
    if all((FRED_DIR / f).exists() for f in ("fred_raw.csv", "fred_chf.csv")):
        return
    skip = pytest.mark.skip(reason="FRED data not found - run: python scripts/fetch_data.py")
    for item in items:
        if {"built", "es_rows"} & set(getattr(item, "fixturenames", [])):
            item.add_marker(skip)
