"""Stage 1: build the database (schema, parameters, synthetic portfolio, sensitivities)."""
import os

from . import db, marketdata, portfolio, sensitivities
from .config import DB_PATH


def curvature_shocks(conn) -> dict:
    """MAR21.99: shock = highest GIRR delta risk weight, applied in parallel to the whole curve. The same
    optional sqrt(2) relief as the delta risk weights (MAR21.44) is carried over; off by default."""
    par = dict(conn.execute("SELECT param_name, value FROM sbm_parameters"))
    top = conn.execute("SELECT MAX(risk_weight) FROM girr_tenor_rw").fetchone()[0] * par["curv_girr_rw_rule"]
    spec = dict(conn.execute("SELECT currency, girr_specified FROM specified_currencies"))
    out = {}
    for (c,) in conn.execute("SELECT DISTINCT bucket FROM risk_factors WHERE risk_class='GIRR'"):
        relief = par["inv_sqrt2"] if par["girr_rw_sqrt2_option"] == 1 and spec.get(c, 0) == 1 else 1.0
        out[c] = top * relief
    return out


def build_database(path=DB_PATH):
    if os.path.exists(path):
        os.remove(path)
    asof, curves, fx, notes = marketdata.latest_curves_and_fx()
    desks, books, instruments, positions = portfolio.generate(curves, fx, str(asof.date()))
    conn = db.connect(path)
    db.create_schema(conn)
    db.load_parameters(conn)
    db.load_portfolio(conn, desks, books, instruments, positions, curves,
                      {c: notes[c] for c in curves}, fx, notes["fx"])
    conn.execute((db.SQL_DIR / "10_book_classification.sql").read_text(encoding="utf-8"))
    inst = {i["instrument_id"]: i for i in instruments}
    rows, crows, vrows = [], [], []
    shocks = curvature_shocks(conn)
    for p in positions:
        drows = sensitivities.position_sensitivities(inst[p["instrument_id"]], p["quantity"], curves, fx)
        for r in drows:
            r["position_id"] = p["position_id"]
            rows.append(r)
        for r in sensitivities.position_curvature(inst[p["instrument_id"]], p["quantity"], curves, fx, shocks, drows):
            r["position_id"] = p["position_id"]
            crows.append(r)
        for r in sensitivities.position_vega(inst[p["instrument_id"]], p["quantity"], curves, fx):
            r["position_id"] = p["position_id"]
            vrows.append(r)
    db.load_sensitivities(conn, rows)
    db.load_curvature(conn, crows)
    db.load_vega(conn, vrows)
    db.load_limits(conn)
    conn.commit()
    return conn, dict(asof=asof, curves=curves, fx=fx, instruments=instruments, positions=positions)
