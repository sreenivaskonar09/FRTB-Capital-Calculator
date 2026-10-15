"""SBM engine (delta, vega, curvature): SQL does the netting, weighting and reporting; Python does the quadratic forms.

Pure maths (testable without a database):
    scale_corr(), girr_corr_matrix(), vega_corr_matrix(), bucket_kb(), delta_charge(), curvature_charge()
Pipeline (reads/writes the SQLite tables):
    run_basis(conn, basis)
"""
import math

import numpy as np
import pandas as pd

from .config import SCENARIOS, SQL_DIR, TENORS


# ----------------------------------------------------------------------------- pure maths
def scale_corr(x, scenario: str, p: dict):
    """MAR21.6: correlation under the high / medium / low scenario. Works on scalars or arrays."""
    x = np.asarray(x, float)
    if scenario == "medium":
        return x
    if scenario == "high":
        return np.minimum(x * p["scen_high_mult"], p["scen_high_cap"])
    if scenario == "low":
        return np.maximum(p["scen_low_a"] * x - 1.0, p["scen_low_b"] * x)
    raise ValueError(scenario)


def girr_corr_matrix(tenors, base_corr: pd.DataFrame, scenario: str, p: dict) -> np.ndarray:
    """Tenor correlation matrix (single curve per currency) from the Table 2 values in SQL,
    scaled to the scenario. Diagonal is 1 in every scenario."""
    R = np.array([[base_corr.loc[(a, b), "rho"] for b in tenors] for a in tenors])
    R = scale_corr(R, scenario, p)
    np.fill_diagonal(R, 1.0)
    return R


def bucket_kb(ws: np.ndarray, R: np.ndarray) -> float:
    """MAR21.4(4): Kb = sqrt(max(0, sum WS^2 + sum_{k!=l} rho WS_k WS_l)) = sqrt(max(0, WS' R WS))."""
    ws = np.asarray(ws, float)
    return float(np.sqrt(max(0.0, ws @ R @ ws)))


def delta_charge(kb: dict, sb: dict, gamma: float):
    """MAR21.4(5): across-bucket aggregation with one uniform gamma for every bucket pair.
    Returns (charge, used_alternative_sb, sb_used dict)."""
    buckets = list(kb)

    def inner(s):
        tot = sum(kb[b] ** 2 for b in buckets)
        tot += sum(gamma * s[b] * s[c] for b in buckets for c in buckets if b != c)
        return tot

    tot = inner(sb)
    used_alt = False
    s_used = dict(sb)
    if tot < 0:   # MAR21.4(5)(b) alternative specification
        used_alt = True
        s_used = {b: max(min(sb[b], kb[b]), -kb[b]) for b in buckets}
        tot = inner(s_used)
    return float(np.sqrt(max(0.0, tot))), used_alt, s_used


def vega_corr_matrix(factors, scenario: str, p: dict) -> np.ndarray:
    """MAR21.93: rho_kl = min( exp(-a*|Te_k-Te_l|/min(Te_k,Te_l)) * exp(-a*|Tu_k-Tu_l|/min(Tu_k,Tu_l)), 1 ),
    a = 1%, Te = option maturity, Tu = residual maturity of the underlying. factors: [(Te, Tu), ...].
    Then the MAR21.6 scenario scaling is applied to rho; the diagonal stays 1."""
    a = p["vega_alpha"]
    n = len(factors)
    R = np.ones((n, n))
    for i, (te_i, tu_i) in enumerate(factors):
        for j, (te_j, tu_j) in enumerate(factors):
            if i != j:
                R[i, j] = min(math.exp(-a * abs(te_i - te_j) / min(te_i, te_j))
                              * math.exp(-a * abs(tu_i - tu_j) / min(tu_i, tu_j)), 1.0)
    R = scale_corr(R, scenario, p)
    np.fill_diagonal(R, 1.0)
    return R


def compute_vega(ws: pd.DataFrame, p: dict):
    """ws columns: scope, bucket, option_tenor, underlying_tenor, weighted_sensitivity.
    Returns (bucket_rows, scenario_rows). Same aggregation as delta (MAR21.4(4)-(5))."""
    bucket_rows, scen_rows = [], []
    for scope, g_scope in ws.groupby("scope"):
        for scen in SCENARIOS:
            kb, sb = {}, {}
            for bucket, g_b in g_scope.groupby("bucket"):
                factors = list(zip(g_b["option_tenor"], g_b["underlying_tenor"]))
                kb[bucket] = bucket_kb(g_b["weighted_sensitivity"].to_numpy(), vega_corr_matrix(factors, scen, p))
                sb[bucket] = float(g_b["weighted_sensitivity"].sum())
            g = float(scale_corr(p["vega_gamma_across_ccy"], scen, p))
            charge, alt, s_used = delta_charge(kb, sb, g)
            for b in kb:
                bucket_rows.append(dict(scope=scope, scenario=scen, bucket=b, kb=kb[b], sb_raw=sb[b], sb_used=s_used[b]))
            scen_rows.append(dict(scope=scope, scenario=scen, risk_class="GIRR", vega_charge=charge,
                                  used_alt_sb=int(alt)))
    return bucket_rows, scen_rows


def curvature_charge(buckets: dict, gamma: float):
    """GIRR curvature aggregation, MAR21.5(3)-(4). buckets: {bucket: (CVR_up, CVR_dn)}.

    Each currency is one bucket with ONE curvature risk factor (MAR21.8(5)(a)), so the within-bucket
    correlation (delta rho squared, MAR21.100) never enters: K_b+ = max(CVR+, 0), K_b- = max(CVR-, 0).
    K_b = max(K_b+, K_b-); the scenario with the larger K is selected (tie: up if CVR+ > CVR-), and
    S_b = the CVR of the selected scenario. Across buckets psi(S_b, S_c) = 0 when both are negative.
    Returns (charge, detail rows)."""
    detail, kb, sb = [], {}, {}
    for b, (up, dn) in buckets.items():
        k_up, k_dn = max(up, 0.0), max(dn, 0.0)
        use_up = k_up > k_dn or (k_up == k_dn and up > dn)
        kb[b], sb[b] = max(k_up, k_dn), (up if use_up else dn)
        detail.append(dict(bucket=b, k_up=k_up, k_dn=k_dn, selected="up" if use_up else "down", kb=kb[b], sb=sb[b]))
    tot = sum(v * v for v in kb.values())
    for b in kb:
        for c in kb:
            if b != c and not (sb[b] < 0 and sb[c] < 0):      # psi
                tot += gamma * sb[b] * sb[c]
    return float(np.sqrt(max(0.0, tot))), detail


def curvature_gamma(p: dict, scenario: str) -> float:
    """MAR21.101 / 21.100: gamma_curv = (delta gamma)^2 = 25%, and the high/low scenarios are applied to
    that squared value (not to the delta gamma before squaring)."""
    return float(scale_corr(p["girr_gamma_across_ccy"] ** p["curv_corr_power"], scenario, p))


# ----------------------------------------------------------------------------- pipeline
def _read_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def load_params(conn) -> dict:
    return dict(conn.execute("SELECT param_name, value FROM sbm_parameters").fetchall())


def compute_charges(ws: pd.DataFrame, p: dict, base_corr: pd.DataFrame):
    """ws columns: scope, risk_class, bucket, tenor_years, weighted_sensitivity.
    Returns (bucket_rows, scenario_rows) as lists of dict, for every scope x scenario x class."""
    bucket_rows, scen_rows = [], []
    gamma = {"GIRR": p["girr_gamma_across_ccy"], "FX": p["fx_gamma_across_buckets"]}
    for scope, g_scope in ws.groupby("scope"):
        for scen in SCENARIOS:
            for rc, g_rc in g_scope.groupby("risk_class"):
                kb, sb = {}, {}
                for bucket, g_b in g_rc.groupby("bucket"):
                    if rc == "GIRR":
                        vec = np.zeros(len(TENORS))
                        for t, w in zip(g_b["tenor_years"], g_b["weighted_sensitivity"]):
                            vec[TENORS.index(float(t))] += w
                        k = bucket_kb(vec, girr_corr_matrix(TENORS, base_corr, scen, p))
                    else:  # FX: one risk factor per bucket -> Kb = |WS|
                        k = abs(float(g_b["weighted_sensitivity"].sum()))
                    kb[bucket] = k
                    sb[bucket] = float(g_b["weighted_sensitivity"].sum())
                g = float(scale_corr(gamma[rc], scen, p))
                charge, alt, s_used = delta_charge(kb, sb, g)
                for b in kb:
                    bucket_rows.append(dict(scope=scope, scenario=scen, risk_class=rc, bucket=b,
                                            kb=kb[b], sb_raw=sb[b], sb_used=s_used[b]))
                scen_rows.append(dict(scope=scope, scenario=scen, risk_class=rc,
                                      delta_charge=charge, used_alt_sb=int(alt)))
    return bucket_rows, scen_rows


def run_basis(conn, basis: str) -> None:
    """Net -> weight -> aggregate -> pick binding scenario -> limits, for one basis."""
    for tbl in ("res_net_sens", "res_weighted_sens", "res_bucket_kb", "res_delta_scenario",
                "res_sbm_final", "res_sbm_components", "res_limit_util"):
        conn.execute(f"DELETE FROM {tbl} WHERE basis = ?", (basis,))
    conn.execute(_read_sql("20_net_sensitivities.sql"), {"basis": basis})
    conn.execute(_read_sql("30_weighted_sensitivities.sql"), {"basis": basis})
    ws = pd.read_sql("SELECT * FROM res_weighted_sens WHERE basis = ?", conn, params=(basis,))
    p = load_params(conn)
    corr = pd.read_sql("SELECT tenor_k, tenor_l, rho FROM girr_tenor_corr", conn).set_index(["tenor_k", "tenor_l"])
    b_rows, s_rows = compute_charges(ws, p, corr)
    conn.executemany(
        "INSERT INTO res_bucket_kb VALUES (?,?,?,?,?,?,?,?)",
        [(basis, r["scope"], r["scenario"], r["risk_class"], r["bucket"], r["kb"], r["sb_raw"], r["sb_used"])
         for r in b_rows])
    conn.executemany(
        "INSERT INTO res_delta_scenario VALUES (?,?,?,?,?,?)",
        [(basis, r["scope"], r["scenario"], r["risk_class"], r["delta_charge"], r["used_alt_sb"])
         for r in s_rows])
    run_vega(conn, basis, p)
    run_curvature(conn, basis, p)
    conn.execute(_read_sql("50_final_charge.sql"), {"basis": basis})
    conn.execute(_read_sql("55_components.sql"), {"basis": basis})
    conn.execute(_read_sql("60_limit_utilisation.sql"), {"basis": basis})
    conn.commit()


def run_vega(conn, basis: str, p: dict) -> None:
    """Net and weight GIRR vega in SQL, aggregate in Python (three correlation scenarios)."""
    for tbl in ("res_net_vega", "res_weighted_vega", "res_vega_bucket", "res_vega_scenario"):
        conn.execute(f"DELETE FROM {tbl} WHERE basis = ?", (basis,))
    conn.execute(_read_sql("26_net_vega.sql"), {"basis": basis})
    conn.execute(_read_sql("31_weighted_vega.sql"), {"basis": basis})
    ws = pd.read_sql("SELECT * FROM res_weighted_vega WHERE basis = ?", conn, params=(basis,))
    if ws.empty:
        return
    b_rows, s_rows = compute_vega(ws, p)
    conn.executemany("INSERT INTO res_vega_bucket VALUES (?,?,?,?,?,?,?)",
                     [(basis, r["scope"], r["scenario"], r["bucket"], r["kb"], r["sb_raw"], r["sb_used"]) for r in b_rows])
    conn.executemany("INSERT INTO res_vega_scenario VALUES (?,?,?,?,?,?)",
                     [(basis, r["scope"], r["scenario"], r["risk_class"], r["vega_charge"], r["used_alt_sb"])
                      for r in s_rows])


def run_curvature(conn, basis: str, p: dict) -> None:
    """Net CVRs in SQL, aggregate in Python, for cmode OPT (headline) and ALL (MAR21.2(4) variant)."""
    for tbl in ("res_net_curvature", "res_curv_bucket", "res_curvature_scenario"):
        conn.execute(f"DELETE FROM {tbl} WHERE basis = ?", (basis,))
    for cmode in ("OPT", "ALL"):
        conn.execute(_read_sql("25_net_curvature.sql"), {"basis": basis, "cmode": cmode})
        net = pd.read_sql("SELECT scope, bucket, cvr_up, cvr_dn FROM res_net_curvature WHERE basis=? AND cmode=?",
                          conn, params=(basis, cmode))
        for scope, g in net.groupby("scope"):
            buckets = {r.bucket: (r.cvr_up, r.cvr_dn) for r in g.itertuples()}
            for scen in SCENARIOS:
                charge, detail = curvature_charge(buckets, curvature_gamma(p, scen))
                conn.execute("INSERT INTO res_curvature_scenario VALUES (?,?,?,?,?,?)",
                             (basis, scope, cmode, scen, "GIRR", charge))
                conn.executemany("INSERT INTO res_curv_bucket VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 [(basis, scope, cmode, scen, d["bucket"], d["k_up"], d["k_dn"], d["selected"],
                                   d["kb"], d["sb"]) for d in detail])
