"""Independent pandas/loop reconciliation of the SQL + matrix pipeline.

Deliberately shares NO calculation code with frtb.sbm or the SQL files:
  * reads raw `sensitivities` + org tables and nets with pandas groupby,
  * reads risk weights / correlations straight from config/*.csv (not from the SQL parameter tables),
  * computes Kb and the across-bucket aggregation with explicit double loops (not a matrix product).
Also reports the (small) capital effect of using the rounded Table 2 versus the closed-form footnote-13 formula.
"""
import math

import pandas as pd

from .config import CONFIG_DIR, TENORS


def _cfg():
    rw = pd.read_csv(CONFIG_DIR / "girr_tenor_rw.csv").set_index("tenor_years")["risk_weight"].to_dict()
    corr = pd.read_csv(CONFIG_DIR / "girr_tenor_corr.csv").set_index(["tenor_k", "tenor_l"])["rho"].to_dict()
    sc = pd.read_csv(CONFIG_DIR / "scalar_parameters.csv").set_index("param_name")["value"].to_dict()
    return rw, corr, sc


def _scen(x, scen):
    if scen == "medium":
        return x
    if scen == "high":
        return min(1.25 * x, 1.0)
    return max(2 * x - 1.0, 0.75 * x)


def _charge(ws_by_bucket, rho_fn, gamma, scen):
    """ws_by_bucket: {bucket: {tenor_or_None: WS}}. Explicit loops straight from MAR21.4."""
    kb, sb = {}, {}
    for b, d in ws_by_bucket.items():
        keys = list(d)
        s2 = sum(d[k] ** 2 for k in keys)
        cross = 0.0
        for a in keys:
            for c in keys:
                if a != c:
                    cross += rho_fn(a, c, scen) * d[a] * d[c]
        kb[b] = math.sqrt(max(0.0, s2 + cross))
        sb[b] = sum(d.values())
    g = _scen(gamma, scen)

    def inner(s):
        tot = sum(v * v for v in kb.values())
        for b in kb:
            for c in kb:
                if b != c:
                    tot += g * s[b] * s[c]
        return tot

    t = inner(sb)
    if t < 0:
        t = inner({b: max(min(sb[b], kb[b]), -kb[b]) for b in kb})
    return math.sqrt(max(0.0, t))


def reconcile(conn, basis: str, use_formula_corr: bool = False) -> pd.DataFrame:
    """Return a table of independent vs SQL charges for every scope x risk class x scenario."""
    rw, corr, sc = _cfg()
    sens = pd.read_sql(
        "SELECT d.desk_name, b.trading_book_flag AS tb, s.risk_class, s.bucket, s.risk_factor, "
        "s.tenor_years, s.sensitivity FROM sensitivities s "
        "JOIN positions p USING (position_id) JOIN books b USING (book_id) JOIN desks d USING (desk_id)", conn)
    if basis == "TRADING":
        sens = sens[(sens.tb == 1) | (sens.risk_class == "FX")]

    def rho(a, c, scen):
        base = (max(math.exp(-sc["girr_theta"] * abs(a - c) / min(a, c)), sc["girr_rho_floor"])
                if use_formula_corr else corr[(a, c)])
        return _scen(base, scen)

    out = []
    for scope in ["FIRM"] + sorted(sens.desk_name.unique()):
        s = sens if scope == "FIRM" else sens[sens.desk_name == scope]
        net = s.groupby(["risk_class", "bucket", "risk_factor", "tenor_years"], dropna=False)["sensitivity"] \
               .sum().reset_index()
        for scen in ("high", "medium", "low"):
            for rc in ("GIRR", "FX"):
                n = net[net.risk_class == rc]
                if n.empty:
                    continue
                ws = {}
                for r in n.itertuples():
                    if rc == "GIRR":
                        w = r.sensitivity * rw[r.tenor_years]
                        ws.setdefault(r.bucket, {})[r.tenor_years] = w
                    else:
                        w = r.sensitivity * sc["fx_rw"]
                        ws.setdefault(r.bucket, {})[r.risk_factor] = w
                if rc == "FX":
                    rho_fn, gamma = (lambda a, c, scen: 0.0), sc["fx_gamma_across_buckets"]
                else:
                    rho_fn, gamma = rho, sc["girr_gamma_across_ccy"]
                out.append(dict(scope=scope, risk_class=rc, scenario=scen,
                                independent=_charge(ws, rho_fn, gamma, scen)))
    ind = pd.DataFrame(out)
    sql = pd.read_sql("SELECT scope, risk_class, scenario, delta_charge AS sql_pipeline "
                      "FROM res_delta_scenario WHERE basis = ?", conn, params=(basis,))
    m = ind.merge(sql, on=["scope", "risk_class", "scenario"], how="outer")
    m["abs_diff"] = (m["independent"] - m["sql_pipeline"]).abs()
    return m


def _independent_curvature(conn, basis: str, cmode: str) -> dict:
    """{(scope, scenario): charge}. Explicit loops, MAR21.5(3)-(4) / MAR21.101, from the raw per-position CVRs."""
    _, _, sc = _cfg()
    cv = pd.read_sql(
        "SELECT d.desk_name, b.trading_book_flag AS tb, i.has_optionality AS opt, c.bucket, c.cvr_up, c.cvr_dn "
        "FROM curvature_cvr c JOIN positions p USING (position_id) JOIN instruments i USING (instrument_id) "
        "JOIN books b USING (book_id) JOIN desks d USING (desk_id)", conn)
    if basis == "TRADING":
        cv = cv[cv.tb == 1]
    if cmode == "OPT":
        cv = cv[cv.opt == 1]
    out = {}
    for scope in ["FIRM"] + sorted(cv.desk_name.unique()):
        g = cv if scope == "FIRM" else cv[cv.desk_name == scope]
        net = g.groupby("bucket")[["cvr_up", "cvr_dn"]].sum()
        if net.empty:
            continue
        for scen in ("high", "medium", "low"):
            gam = _scen(sc["girr_gamma_across_ccy"] ** sc["curv_corr_power"], scen)   # square, THEN scenario
            K, S = {}, {}
            for b, r in net.iterrows():
                kp, km = max(r.cvr_up, 0.0), max(r.cvr_dn, 0.0)
                pick_up = (kp > km) or (kp == km and r.cvr_up > r.cvr_dn)
                K[b] = max(kp, km)
                S[b] = r.cvr_up if pick_up else r.cvr_dn
            tot = sum(k * k for k in K.values())
            for b in K:
                for c in K:
                    if b != c and not (S[b] < 0 and S[c] < 0):
                        tot += gam * S[b] * S[c]
            out[(scope, scen)] = math.sqrt(max(0.0, tot))
    return out


def _independent_vega(conn, basis: str) -> dict:
    """{(scope, scenario): charge}. Explicit loops, MAR21.4 + MAR21.92-95, from the raw per-position vega rows."""
    _, _, sc = _cfg()
    rw = min(sc["vega_rw_sigma"] * math.sqrt(sc["vega_lh_girr_days"]) / math.sqrt(10), sc["vega_rw_cap"])
    a = sc["vega_alpha"]
    v = pd.read_sql(
        "SELECT d.desk_name, b.trading_book_flag AS tb, v.bucket, v.option_tenor AS te, v.underlying_tenor AS tu, "
        "v.alloc_weight AS w, v.vega, v.implied_vol AS vol FROM vega_sensitivities v "
        "JOIN positions p USING (position_id) JOIN books b USING (book_id) JOIN desks d USING (desk_id)", conn)
    if basis == "TRADING":
        v = v[v.tb == 1]
    v["s"] = v.w * v.vega * v.vol                       # MAR21.25 recomputed from the raw pieces
    out = {}
    for scope in ["FIRM"] + sorted(v.desk_name.unique()):
        g = v if scope == "FIRM" else v[v.desk_name == scope]
        net = g.groupby(["bucket", "te", "tu"])["s"].sum().reset_index()
        for scen in ("high", "medium", "low"):
            K, S = {}, {}
            for b, nb in net.groupby("bucket"):
                f = list(nb.itertuples())
                ws = [x.s * rw for x in f]
                tot = sum(x * x for x in ws)
                for i, fi in enumerate(f):
                    for j, fj in enumerate(f):
                        if i != j:
                            rho = min(math.exp(-a * abs(fi.te - fj.te) / min(fi.te, fj.te))
                                      * math.exp(-a * abs(fi.tu - fj.tu) / min(fi.tu, fj.tu)), 1.0)
                            tot += _scen(rho, scen) * ws[i] * ws[j]
                K[b], S[b] = math.sqrt(max(0.0, tot)), sum(ws)
            gam = _scen(sc["vega_gamma_across_ccy"], scen)

            def inner(s_):
                t = sum(k * k for k in K.values())
                for b in K:
                    for c in K:
                        if b != c:
                            t += gam * s_[b] * s_[c]
                return t
            t = inner(S)
            if t < 0:
                t = inner({b: max(min(S[b], K[b]), -K[b]) for b in K})
            out[(scope, scen)] = math.sqrt(max(0.0, t))
    return out


def reconcile_vega(conn, basis: str) -> pd.DataFrame:
    ind = pd.Series(_independent_vega(conn, basis), name="independent")
    ind.index = pd.MultiIndex.from_tuples(ind.index, names=["scope", "scenario"])
    sql = pd.read_sql("SELECT scope, scenario, vega_charge AS sql_pipeline FROM res_vega_scenario WHERE basis=?",
                      conn, params=(basis,)).set_index(["scope", "scenario"])
    m = pd.concat([ind, sql], axis=1).fillna(0.0).reset_index()
    m["abs_diff"] = (m["independent"] - m["sql_pipeline"]).abs()
    return m


def reconcile_curvature(conn, basis: str, cmode: str = "OPT") -> pd.DataFrame:
    ind = pd.Series(_independent_curvature(conn, basis, cmode), name="independent")
    ind.index = pd.MultiIndex.from_tuples(ind.index, names=["scope", "scenario"])
    sql = pd.read_sql("SELECT scope, scenario, curvature_charge AS sql_pipeline FROM res_curvature_scenario "
                      "WHERE basis=? AND cmode=?", conn, params=(basis, cmode)).set_index(["scope", "scenario"])
    m = pd.concat([ind, sql], axis=1).fillna(0.0).reset_index()
    m["abs_diff"] = (m["independent"] - m["sql_pipeline"]).abs()
    return m


def totals_match(conn, basis: str, tol: float = 1e-6):
    """Check binding totals (max over scenarios of delta + vega + curvature, summed over risk classes) against
    res_sbm_final. Returns (ok, max abs diff, delta reconciliation frame)."""
    m = reconcile(conn, basis)
    tot = m.groupby(["scope", "scenario"])[["independent", "sql_pipeline"]].sum()
    cm = reconcile_curvature(conn, basis, "OPT").set_index(["scope", "scenario"])
    tot = tot.add(cm[["independent", "sql_pipeline"]], fill_value=0.0)
    vm = reconcile_vega(conn, basis).set_index(["scope", "scenario"])
    tot = tot.add(vm[["independent", "sql_pipeline"]], fill_value=0.0)
    ind = tot["independent"].groupby("scope").max()
    sql = pd.read_sql("SELECT scope, charge_at_binding FROM res_sbm_final WHERE basis=? AND risk_class='TOTAL'",
                      conn, params=(basis,)).set_index("scope")["charge_at_binding"]
    diff = (ind - sql).abs()
    return diff.max() <= tol * max(1.0, sql.max()), diff.max(), m
