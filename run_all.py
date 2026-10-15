"""One command: build DB -> SBM (both bases) -> ES -> reconcile -> tests -> Excel/charts.

    python run_all.py
"""
import subprocess
import sys

from frtb import es, pipeline, reconcile, report, sbm
from frtb.config import DB_PATH, OUT_DIR


def main() -> int:
    print("1/6 building database (schema, BCBS parameters, synthetic portfolio, sensitivities)")
    conn, ctx = pipeline.build_database()
    n = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"    positions={n('positions')} sensitivities={n('sensitivities')} -> {DB_PATH}")

    print("2/6 SBM delta + GIRR vega + GIRR curvature (all positions, and trading book only)")
    for basis in ("ALL", "TRADING"):
        sbm.run_basis(conn, basis)

    print("3/6 97.5% ES, historical simulation, liquidity-horizon cascade")
    conn.executemany("INSERT INTO res_es VALUES (?,?,?)", es.compute_es(conn, ctx))
    conn.commit()

    print("4/6 independent pandas reconciliation")
    for basis in ("ALL", "TRADING"):
        ok, diff, _ = reconcile.totals_match(conn, basis)
        print(f"    {basis}: {'MATCH' if ok else 'MISMATCH'} (max abs total diff {diff:.2e} USD)")
        if not ok:
            return 1

    print("5/6 unit tests")
    rc = subprocess.call([sys.executable, "-m", "pytest", "-q", "tests"])
    if rc != 0:
        return rc

    print("6/6 Excel report and charts")
    path, comp = report.build_workbook(conn, ctx)
    f = comp[(comp.basis == "ALL") & (comp.scope == "FIRM") & (comp.risk_class == "TOTAL")].iloc[0]
    print(f"    {path}\n    Firm SBM (delta+vega+curvature) {f.sa_charge/1e6:,.1f}m | stressed ES {f.es_stressed_lh_scaled/1e6:,.1f}m "
          f"| SA/ES {f.sa_over_es_stressed:.2f}x   (see {OUT_DIR / 'SA_vs_ES_note.md'})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
