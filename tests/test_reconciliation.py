"""Full synthetic portfolio: SQL pipeline vs independent pandas calculation."""
import pytest

from frtb import pipeline, reconcile, sbm


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    conn, _ = pipeline.build_database(tmp_path_factory.mktemp("db") / "t.db")
    for b in ("ALL", "TRADING"):
        sbm.run_basis(conn, b)
    return conn


@pytest.mark.parametrize("basis", ["ALL", "TRADING"])
def test_sql_matches_independent_pandas(built, basis):
    ok, diff, m = reconcile.totals_match(built, basis)
    assert ok, diff
    assert m["abs_diff"].max() < 1e-4            # USD, on charges of ~1e8
    assert m[["independent", "sql_pipeline"]].notna().all().all()


def test_firm_le_sum_of_desks(built):
    firm = built.execute("SELECT charge_at_binding FROM res_sbm_final WHERE basis='ALL' AND scope='FIRM' "
                         "AND risk_class='TOTAL'").fetchone()[0]
    desks = built.execute("SELECT SUM(charge_at_binding) FROM res_sbm_final WHERE basis='ALL' AND "
                          "scope<>'FIRM' AND risk_class='TOTAL'").fetchone()[0]
    assert firm <= desks + 1e-6                  # diversification can only help


def test_removing_banking_book_lowers_capital(built):
    q = "SELECT charge_at_binding FROM res_sbm_final WHERE basis=? AND scope='FIRM' AND risk_class='TOTAL'"
    assert built.execute(q, ("TRADING",)).fetchone()[0] < built.execute(q, ("ALL",)).fetchone()[0]
