"""Excel workbook, charts and the SA-vs-ES note. Every number is read back from SQLite."""
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.drawing.image import Image as XLImage  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from .config import OUT_DIR, SQL_DIR  # noqa: E402

BLUE, ORANGE, GREY, RED = "#2a6fb0", "#d9822b", "#6b7280", "#c0392b"   # colour-blind-safe pair + neutrals
MUSD = 1e6
HDR = PatternFill("solid", fgColor="1F3A5F")
WARN = PatternFill("solid", fgColor="FDE2E0")
OKF = PatternFill("solid", fgColor="E3F1E3")
BAND = PatternFill("solid", fgColor="FFF4D6")

DISCLAIMER = ("EDUCATIONAL IMPLEMENTATION on SYNTHETIC data. Not production-grade, not regulatory-compliant, "
              "not a capital figure for any real institution. GIRR delta, vega and curvature + FX delta only.")


def named_queries() -> dict:
    txt = (SQL_DIR / "70_reports.sql").read_text(encoding="utf-8")
    parts = re.split(r"^-- name: (\w+)\s*$", txt, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def q(conn, name, basis="ALL"):
    sql = named_queries()[name]
    return pd.read_sql(sql, conn, params={"basis": basis} if ":basis" in sql else None)


def comparison_table(conn) -> pd.DataFrame:
    sa = pd.read_sql("SELECT basis, scope, risk_class, binding_scenario, charge_at_binding AS sa_charge "
                     "FROM res_sbm_final", conn)
    e = pd.read_sql("SELECT scope, measure, value FROM res_es WHERE measure NOT LIKE 'meta:%'", conn)
    e[["basis", "risk_class", "m"]] = e["measure"].str.split(":", expand=True)
    e["risk_class"] = e["risk_class"].replace({"ALL": "TOTAL"})
    w = e.pivot_table(index=["basis", "scope", "risk_class"], columns="m", values="value").reset_index()
    t = sa.merge(w, on=["basis", "scope", "risk_class"])
    t["es_x1.5_indicative"] = 1.5 * t["es_stressed_lh_scaled"]
    t["sa_over_es_stressed"] = t["sa_charge"] / t["es_stressed_lh_scaled"]
    t["sa_over_es_x1.5"] = t["sa_charge"] / t["es_x1.5_indicative"]
    t["sa_over_es_current"] = t["sa_charge"] / t["es_current_lh_scaled"]
    t["sa_over_es_volfixed"] = t["sa_charge"] / t["es_stressed_lh_scaled_volfixed"]
    return t


def _style(ws, widths=None, header_row=1):
    for c in ws[header_row]:
        c.fill, c.font = HDR, Font(color="FFFFFF", bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, col in enumerate(ws.columns, 1):
        width = (widths or {}).get(i) or min(max(len(str(c.value)) if c.value is not None else 0 for c in col) + 2, 48)
        ws.column_dimensions[get_column_letter(i)].width = max(width, 10)
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)


def _put(ws, df, start_row=1, money_cols=(), pct_cols=(), ratio_cols=()):
    for j, c in enumerate(df.columns, 1):
        ws.cell(row=start_row, column=j, value=c)
    for i, row in enumerate(df.itertuples(index=False), start_row + 1):
        for j, v in enumerate(row, 1):
            if pd.isna(v):
                v = None
            elif hasattr(v, "item"):
                v = v.item()
            cell = ws.cell(row=i, column=j, value=v)
            name = df.columns[j - 1]
            if name in money_cols:
                cell.number_format = '#,##0.00'
            elif name in pct_cols:
                cell.number_format = '0.0%'
            elif name in ratio_cols:
                cell.number_format = '0.00"x"'


def sa_vs_es_note(conn, comp: pd.DataFrame) -> str:
    f = comp[(comp.basis == "ALL") & (comp.scope == "FIRM")].set_index("risk_class")
    d = comp[(comp.basis == "ALL") & (comp.risk_class == "TOTAL") & (comp.scope != "FIRM")].set_index("scope")
    g = comp[(comp.basis == "ALL") & (comp.risk_class == "GIRR") & (comp.scope == "FX_DESK")].iloc[0]
    vg = float(conn.execute("SELECT charge FROM res_sbm_components WHERE basis='ALL' AND scope='FIRM' "
                            "AND risk_class='TOTAL' AND component='vega'").fetchone()[0])
    ev = dict(conn.execute("SELECT measure, value FROM res_es WHERE scope='FIRM' AND measure LIKE 'ALL:VOL:%'"))
    vo10, voLH = ev["ALL:VOL:es10_stressed"], ev["ALL:VOL:es_stressed_lh_scaled"]
    return f"""SA vs ES: what the comparison shows (partial, educational)

Result (whole book, all positions, USD m): SBM (delta + GIRR vega + GIRR curvature) {f.loc['TOTAL','sa_charge']/MUSD:,.0f} vs stressed liquidity-adjusted ES {f.loc['TOTAL','es_stressed_lh_scaled']/MUSD:,.0f} -> SA is {f.loc['TOTAL','sa_over_es_stressed']:.1f}x ES ({f.loc['TOTAL','sa_over_es_x1.5']:.1f}x if ES is multiplied by the indicative mc = 1.5, MAR33.42). By risk class: GIRR {f.loc['GIRR','sa_over_es_stressed']:.1f}x, FX {f.loc['FX','sa_over_es_stressed']:.1f}x. By desk the ratio runs from {d['sa_over_es_stressed'].min():.1f}x ({d['sa_over_es_stressed'].idxmin()}) to {d['sa_over_es_stressed'].max():.1f}x ({d['sa_over_es_stressed'].idxmax()}). Against the current 12-month ES (a lower-volatility window than the stress period) SA is {f.loc['TOTAL','sa_over_es_current']:.1f}x.

Volatility in both measures: SA includes GIRR vega of {vg/MUSD:,.0f}m ({vg/f.loc['TOTAL','sa_charge']:.1%} of SA); without it SA is {(f.loc['TOTAL','sa_charge']-vg)/f.loc['TOTAL','es_stressed_lh_scaled']:.1f}x ES. The ES shocks swaption vol with a MODELLED proxy (10-day change in the 60-day realised vol of the real USD 5y yield, scaled to today's 90bp, one flat relative shock, sticky strike, no smile) and gives it the 60-day liquidity horizon of MAR33.12. Firm ES with vol shocks is {f.loc['TOTAL','es_stressed_lh_scaled']/MUSD:,.1f}m versus {f.loc['TOTAL','es_stressed_lh_scaled_volfixed']/MUSD:,.1f}m with vol held fixed ({f.loc['TOTAL','es_stressed_lh_scaled']/f.loc['TOTAL','es_stressed_lh_scaled_volfixed']-1:+.1%}). Vol risk on its own is {vo10/MUSD:,.1f}m over 10 days and {voLH/MUSD:,.1f}m after the 60-day horizon scaling (x sqrt(6)), the closest counterpart to the SA vega charge. In the whole-book ES the total barely moves, probably because net-long USD vega gains when the proxy vol rises in a rate stress and offsets rate losses (not separately verified). Illustrative only: the proxy is not an implied-vol history, and in a real IMA vol points often fail the risk factor eligibility test (MAR31.13) and become non-modellable.

Why SA is more punitive here
1. Prescribed risk weights are fixed, not calibrated to this portfolio. The FX weight of 15% (MAR21.87) is applied to every net exposure; the historical 10-day stressed ES of the actual EUR/GBP/CHF moves is far smaller. GIRR weights of 1.1-1.7% per vertex are calibrated to the liquidity horizon of the risk class (MAR21.40) and are applied to every vertex independently.
2. The binding correlation scenario is 'high' for the whole book (MAR21.6): the long-biased book pays for correlations scaled up by 25%, whereas historical simulation uses the correlations that actually occurred.
3. Offsets are only recognised where the standard says so: hedges across tenors are only partly recognised (Table 2 correlations < 100%), and across currencies only at gamma = 50% (GIRR) / 60% (FX). ES recognises whatever offset the data show. The {g['scope']} GIRR line shows the extreme case: a nearly-hedged FX-forward book has a small real P&L risk (ES {g['es_stressed_lh_scaled']/MUSD:.2f}m) but SA charges {g['sa_charge']/MUSD:.2f}m because forward legs in different currency buckets and tenors cannot net.
4. Full revaluation vs sensitivities: ES revalues every position, so it captures the convexity and gamma of the swaptions directly; SA captures only part of it through the GIRR curvature charge, which is small here because the USD options are net long gamma and offset the short-gamma EUR book only partially. Both measures now see option vol risk, but SA does so with prescribed weights and correlations while the ES uses an assumed (proxy) vol history.

Where SA could be less punitive (not seen in this book)
SA ignores the volatility regime and any move outside its prescribed shocks and risk that history did not contain; a book with large option positions, or concentrated in a tail event outside the 2007-2026 window, could show ES > SA. SA also ignores the actual current volatility regime, so it can understate in a stressed-vol start state and overstate in a calm one (see the current-window ratio).

Differences in scope: this is a PARTIAL comparison
- Not modelled in either measure: default risk charge (DRC), residual risk add-on (RRAO), credit-spread risk on the government bonds (they would carry CSR in a real SA), equity/commodity, inflation and cross-currency-basis GIRR factors, and FX curvature (no FX options). Swaption vol in the ES is a MODELLED proxy, not an implied-vol history, and is a single flat relative shock.
- IMA side is not a full IMCC: no rho = 0.5 blend of constrained/unconstrained ES across risk classes (MAR33.15), no non-modellable risk factor capital (SES), no backtesting/PLA add-ons or desk eligibility, and the 1.5 multiplier is shown only as an indicative column.
- The ES stress period is the worst rolling 250-observation window of 10-day overlapping changes since 2007 for this portfolio (MAR33.7 idea), without the reduced-factor-set scaling of MAR33.6 because the full and reduced sets coincide here.
- Data: USD rates and FX history are real FRED series; EUR/GBP/CHF rate histories are MODELLED (beta x USD changes plus small noise) and their curves are ILLUSTRATIVE, so non-USD GIRR ES is only as good as that assumption. CHF interest rates (unspecified currency, 20-day horizon, MAR33.12) are the only factors that exercise the liquidity-horizon cascade.
- Portfolio is synthetic; limits are hand-set round numbers to demonstrate monitoring.
"""


# ----------------------------------------------------------------------------- charts
def _bar_labels(ax, fmt="{:,.0f}"):
    for p in ax.patches:
        h = p.get_height()
        if h > 0:
            ax.annotate(fmt.format(h), (p.get_x() + p.get_width() / 2, p.get_y() + h), ha="center",
                        va="bottom", fontsize=8, color="#222", xytext=(0, 2), textcoords="offset points")


def make_charts(conn, comp):
    OUT_DIR.mkdir(exist_ok=True)
    paths = {}
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

    # 1 capital by desk, stacked by risk class (binding scenario, standalone desks)
    d = pd.read_sql("SELECT scope AS desk, risk_class || ' ' || component AS part, charge FROM res_sbm_components "
                    "WHERE basis='ALL' AND scope <> 'FIRM' AND risk_class <> 'TOTAL'", conn)
    d = d.pivot_table(index="desk", columns="part", values="charge", aggfunc="sum").fillna(0) / MUSD
    d = d.reindex(columns=["GIRR delta", "GIRR vega", "GIRR curvature", "FX delta"]).fillna(0)
    d = d.loc[d.sum(axis=1).sort_values(ascending=False).index]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    d.plot(kind="bar", stacked=True, ax=ax, color=[BLUE, "#2a9d8f", "#8fb8de", ORANGE], width=0.65)
    ax.set_ylabel("SBM charge, USD m"); ax.set_xlabel("")
    ax.set_title("SBM capital by desk (standalone, binding scenario)", loc="left", fontsize=11)
    ax.tick_params(axis="x", rotation=0)
    for i, tot in enumerate(d.sum(axis=1)):
        ax.annotate(f"{tot:,.0f}", (i, tot), ha="center", va="bottom", fontsize=9, xytext=(0, 2),
                    textcoords="offset points")
    ax.legend(frameon=False, title=None)
    fig.tight_layout(); p = OUT_DIR / "chart_capital_by_desk.png"; fig.savefig(p, dpi=160); plt.close(fig)
    paths["desk"] = p

    # 2 SA vs ES by scope
    c = comp[(comp.basis == "ALL") & (comp.risk_class == "TOTAL")].set_index("scope")
    order = ["FIRM"] + [s for s in c.index if s != "FIRM"]
    c = c.loc[order]
    fig, ax = plt.subplots(figsize=(8, 4.4))
    x = range(len(c)); w = 0.38
    ax.bar([i - w / 2 for i in x], c.sa_charge / MUSD, w, color=BLUE, label="SA: SBM delta (GIRR+FX)")
    ax.bar([i + w / 2 for i in x], c.es_stressed_lh_scaled / MUSD, w, color=ORANGE,
           label="ES 97.5%, stressed, LH-adjusted (no mc)")
    _bar_labels(ax)
    ax.set_xticks(list(x)); ax.set_xticklabels(c.index, rotation=0, fontsize=9)
    ax.set_ylabel("USD m"); ax.legend(frameon=False)
    ax.set_title("SA vs ES on the same synthetic portfolio (partial comparison)", loc="left", fontsize=11)
    fig.tight_layout(); p = OUT_DIR / "chart_sa_vs_es.png"; fig.savefig(p, dpi=160); plt.close(fig)
    paths["saes"] = p

    # 3 scenarios for the firm
    s = q(conn, "scenarios")
    s = s[(s.scope == "FIRM")].pivot(index="risk_class", columns="scenario", values="delta_charge") / MUSD
    s = s.reindex(columns=["low", "medium", "high"])
    fig, ax = plt.subplots(figsize=(6.5, 4))
    s.plot(kind="bar", ax=ax, color=[GREY, "#8fb8de", BLUE], width=0.7)
    ax.set_ylabel("USD m"); ax.set_xlabel(""); ax.tick_params(axis="x", rotation=0)
    ax.set_title("Firm delta charge under the three correlation scenarios", loc="left", fontsize=11)
    ax.legend(frameon=False, title="scenario")
    fig.tight_layout(); p = OUT_DIR / "chart_scenarios.png"; fig.savefig(p, dpi=160); plt.close(fig)
    paths["scen"] = p

    # 4 limit utilisation
    u = pd.read_sql("SELECT desk_name, risk_class, utilisation, breach FROM res_limit_util WHERE basis='ALL'", conn)
    u["label"] = u.desk_name + " " + u.risk_class
    u = u.sort_values("utilisation")
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.barh(u.label, u.utilisation * 100, color=[RED if b else BLUE for b in u.breach])
    ax.axvline(100, color="#222", lw=1, ls="--")
    ax.set_xlabel("utilisation, % of limit (red = breach)")
    ax.set_title("Limit utilisation by desk and risk class", loc="left", fontsize=11)
    fig.tight_layout(); p = OUT_DIR / "chart_limit_utilisation.png"; fig.savefig(p, dpi=160); plt.close(fig)
    paths["limits"] = p
    return paths


# ----------------------------------------------------------------------------- workbook
def build_workbook(conn, ctx, path=OUT_DIR / "FRTB_SBM_report.xlsx"):
    OUT_DIR.mkdir(exist_ok=True)
    comp = comparison_table(conn)
    charts = make_charts(conn, comp)
    note = sa_vs_es_note(conn, comp)
    (OUT_DIR / "SA_vs_ES_note.md").write_text(note, encoding="utf-8")
    wb = Workbook()

    # Summary
    ws = wb.active; ws.title = "Summary"
    ws["A1"] = "FRTB Standardised Approach (SBM: GIRR delta, vega, curvature; FX delta) - educational calculator"; ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = DISCLAIMER; ws["A2"].fill = BAND
    ws["A3"] = f"As-of date: {ctx['asof'].date()}   Reporting currency: USD   Parameters: BCBS d457 (Jan 2019)"
    cp = pd.read_sql("SELECT basis, risk_class, component, binding_scenario, charge FROM res_sbm_components "
                     "WHERE scope='FIRM'", conn)
    rows = []
    for b, label in (("ALL", "All positions as booked"), ("TRADING", "Trading book only (banking-book GIRR excluded; FX kept, MAR12.6)")):
        sub = cp[cp.basis == b]
        g = lambda rc, comp: float(sub[(sub.risk_class == rc) & (sub.component == comp)]["charge"].sum())
        rows.append((label, g("GIRR", "delta"), g("GIRR", "vega"), g("GIRR", "curvature"), g("FX", "delta"),
                     g("TOTAL", "delta") + g("TOTAL", "vega") + g("TOTAL", "curvature"),
                     sub.binding_scenario.iloc[0]))
    summ = pd.DataFrame(rows, columns=["Basis", "GIRR delta", "GIRR vega", "GIRR curvature", "FX delta", "Total SBM", "Binding scenario"])
    summ["Total SBM (USD m)"] = summ["Total SBM"] / MUSD
    _put(ws, summ, start_row=5, money_cols=("GIRR delta", "GIRR vega", "GIRR curvature", "FX delta", "Total SBM", "Total SBM (USD m)"))
    r = 5 + len(summ) + 2
    t = comp[(comp.scope == "FIRM") & (comp.risk_class == "TOTAL")].set_index("basis")
    ws.cell(row=r, column=1, value="Banking-book capital effect (SBM delta + vega + curvature, USD)").font = Font(bold=True)
    ws.cell(row=r + 1, column=1, value="Capital falls by excluding banking-book GIRR")
    ws.cell(row=r + 1, column=2, value=float(t.loc["ALL", "sa_charge"] - t.loc["TRADING", "sa_charge"])).number_format = '#,##0'
    ws.cell(row=r + 1, column=3, value=float(1 - t.loc["TRADING", "sa_charge"] / t.loc["ALL", "sa_charge"])).number_format = '0.0%'
    ws.cell(row=r + 3, column=1, value="SA vs ES (whole book, all positions)").font = Font(bold=True)
    a = t.loc["ALL"]
    for k, (lbl, val, fmt) in enumerate([("SA SBM (delta + GIRR vega + GIRR curvature)", a.sa_charge, '#,##0'),
                                         ("ES 97.5% stressed, LH-adjusted", a.es_stressed_lh_scaled, '#,##0'),
                                         ("SA / ES", a.sa_over_es_stressed, '0.00"x"'),
                                         ("SA / (1.5 x ES), indicative", a["sa_over_es_x1.5"], '0.00"x"')]):
        ws.cell(row=r + 4 + k, column=1, value=lbl)
        ws.cell(row=r + 4 + k, column=2, value=float(val)).number_format = fmt
    ws.column_dimensions["A"].width = 62
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 22
    ws.add_image(XLImage(str(charts["desk"])), "J5")
    ws.add_image(XLImage(str(charts["saes"])), "J28")

    # SBM by desk and class
    ws = wb.create_sheet("SBM_by_desk_class")
    bd = q(conn, "by_desk_class"); bd["charge_usd_m"] = bd["charge"] / MUSD
    _put(ws, bd, money_cols=("charge", "charge_usd_m")); _style(ws)
    fd = q(conn, "diversification")
    ws.cell(row=len(bd) + 4, column=1, value="Diversification (TOTAL, binding scenario)").font = Font(bold=True)
    for i, rr in enumerate(fd.itertuples(index=False), len(bd) + 5):
        ws.cell(row=i, column=1, value=rr[0]); ws.cell(row=i, column=2, value=float(rr[1])).number_format = '#,##0'
    ws.add_image(XLImage(str(charts["desk"])), "G2")

    # Scenarios
    ws = wb.create_sheet("Scenarios")
    sc = q(conn, "scenarios").pivot_table(index=["scope", "risk_class"], columns="scenario", values="delta_charge").reset_index()
    sc = sc[["scope", "risk_class", "high", "medium", "low"]]
    sc["binding"] = sc[["high", "medium", "low"]].idxmax(axis=1)
    _put(ws, sc, money_cols=("high", "medium", "low")); _style(ws)
    ws.cell(row=len(sc) + 3, column=1, value="Binding = scenario with the largest charge for that row. The regulatory binding "
            "scenario is chosen on the TOTAL across risk classes per scope (MAR21.7), so a class row can differ.")
    ws.add_image(XLImage(str(charts["scen"])), "I2")

    # Limits
    ws = wb.create_sheet("Limits")
    lu = pd.read_sql("SELECT desk_name, risk_class, limit_amount, charge, utilisation, "
                     "CASE breach WHEN 1 THEN 'BREACH' ELSE 'ok' END AS status "
                     "FROM res_limit_util WHERE basis='ALL' ORDER BY utilisation DESC", conn)
    _put(ws, lu, money_cols=("limit_amount", "charge"), pct_cols=("utilisation",)); _style(ws)
    for i in range(2, len(lu) + 2):
        ws.cell(row=i, column=6).fill = WARN if lu.iloc[i - 2]["status"] == "BREACH" else OKF
    ws.cell(row=len(lu) + 3, column=1, value="Limits are SYNTHETIC round numbers set to illustrate monitoring; "
            "charge = desk standalone SBM delta at the desk's binding scenario.")
    ws.add_image(XLImage(str(charts["limits"])), "H2")

    # Trading vs banking
    ws = wb.create_sheet("Trading_vs_Banking")
    bf = q(conn, "book_flags")
    bf["trading_book_flag"] = bf["trading_book_flag"].map({1: "TRADING", 0: "BANKING"})
    _put(ws, bf); _style(ws)
    r0 = len(bf) + 4
    tb = pd.read_sql("SELECT basis, scope, risk_class, charge_at_binding AS charge FROM res_sbm_final "
                     "WHERE risk_class IN ('GIRR','FX','TOTAL') AND scope='FIRM'", conn)
    tb = tb.pivot(index="risk_class", columns="basis", values="charge").reset_index()
    tb["effect_of_excluding_banking_book"] = tb["TRADING"] - tb["ALL"]
    ws.cell(row=r0 - 1, column=1, value="Firm SBM delta: all positions (ALL) vs trading book only (TRADING)").font = Font(bold=True)
    _put(ws, tb, start_row=r0, money_cols=("ALL", "TRADING", "effect_of_excluding_banking_book"))
    ws.cell(row=r0 + len(tb) + 2, column=1, value="Rules: RBC25.3/25.5/25.7/25.9 (see sql/10_book_classification.sql). "
            "Banking-book FX stays in market risk capital (MAR12.6), so only GIRR falls away. The 365-day review "
            "heuristic is NOT from the BCBS text.")

    # Curvature
    ws = wb.create_sheet("Curvature")
    ws["A1"] = ("GIRR curvature (MAR21.5, MAR21.99-101). Shock = parallel shift of a whole currency curve by the highest delta "
                "RW (1.7%). CVR = -(V(shocked) - V - RW*s). Required scope = instruments with optionality (here: swaptions).")
    ws["A1"].alignment = Alignment(wrap_text=True)
    ws.row_dimensions[1].height = 48
    nc = pd.read_sql("SELECT scope, cmode, bucket AS currency, cvr_up, cvr_dn, n_positions FROM res_net_curvature "
                     "WHERE basis='ALL' ORDER BY cmode DESC, scope, bucket", conn)
    nc["scope_of_instruments"] = nc.cmode.map({"OPT": "options only (required)", "ALL": "all instruments (MAR21.2(4) variant)"})
    nc = nc[["scope", "scope_of_instruments", "currency", "cvr_up", "cvr_dn", "n_positions"]]
    ws.cell(row=3, column=1, value="Net CVR by currency (positive = loss beyond delta)").font = Font(bold=True)
    _put(ws, nc, start_row=4, money_cols=("cvr_up", "cvr_dn"))
    r0 = 4 + len(nc) + 3
    ws.cell(row=r0 - 1, column=1, value="Curvature charge by scenario (note: the high-correlation scenario is NOT always the "
            "worst for curvature - a long-gamma bucket offsets a short-gamma one more when gamma is higher)").font = Font(bold=True)
    cs = pd.read_sql("SELECT scope, cmode, scenario, curvature_charge FROM res_curvature_scenario WHERE basis='ALL'", conn)
    cs = cs.pivot_table(index=["scope", "cmode"], columns="scenario", values="curvature_charge").reset_index()
    cs = cs.reindex(columns=["scope", "cmode", "high", "medium", "low"])
    _put(ws, cs, start_row=r0, money_cols=("high", "medium", "low"))
    r1 = r0 + len(cs) + 3
    ws.cell(row=r1 - 1, column=1, value="Delta vs curvature at each scope's binding scenario (total = delta + curvature, MAR21.7)").font = Font(bold=True)
    dc = pd.read_sql("SELECT scope, risk_class, component, binding_scenario, charge FROM res_sbm_components "
                     "WHERE basis='ALL' AND risk_class='TOTAL' ORDER BY scope, component", conn)
    _put(ws, dc, start_row=r1, money_cols=("charge",))
    r2 = r1 + len(dc) + 3
    for i, line in enumerate([
            "Reading this:",
            "- Long options are long gamma, so their CVR is negative (delta overstates the loss): no charge. Short options give a positive CVR.",
            "- Curvature is bucket-wise: each currency is one bucket and one risk factor, so intra-bucket correlation never enters (MAR21.8(5)(a)).",
            "- Across currencies gamma = 50%^2 = 25%; the high/low scenarios are applied to that squared value (MAR21.101).",
            "- psi = 0 when both buckets' S are negative, so two long-gamma currencies do not offset each other.",
            "- Holistic variant (MAR21.2(4)): including linear bonds/swaps (net long convexity) here REDUCES the charge to zero - the required "
            "scope is options only, and the standard requires the variant to be applied consistently to every instrument.",
            "- Vega is charged separately (sheet Vega); curvature only adds the gamma the delta charge misses."], r2):
        ws.cell(row=i, column=1, value=line)
    ws.column_dimensions["A"].width = 24
    for col in "BCDEF":
        ws.column_dimensions[col].width = 26

    # Vega
    ws = wb.create_sheet("Vega")
    ws["A1"] = ("GIRR vega (MAR21.25, MAR21.92-95). s = vega x implied vol (identical under normal and lognormal, MAR21.28 fn9); "
                "RW = min(55% x sqrt(60)/sqrt(10), 100%) = 100%; rho = min(exp(-1% |dTe|/min Te) x exp(-1% |dTu|/min Tu), 1); "
                "gamma across currencies 50%. Swaptions are mapped to the 0.5/1/3/5/10y grid by bilinear interpolation (assumption).")
    ws["A1"].alignment = Alignment(wrap_text=True)
    ws.row_dimensions[1].height = 62
    vl = pd.read_sql("SELECT bucket AS currency, option_tenor, underlying_tenor, net_sensitivity, risk_weight, "
                     "weighted_sensitivity FROM res_weighted_vega WHERE basis='ALL' AND scope='FIRM' "
                     "ORDER BY bucket, option_tenor, underlying_tenor", conn)
    ws.cell(row=3, column=1, value="Firm net vega ladder (USD)").font = Font(bold=True)
    _put(ws, vl, start_row=4, money_cols=("net_sensitivity", "weighted_sensitivity"), pct_cols=("risk_weight",))
    r0 = 4 + len(vl) + 3
    ws.cell(row=r0 - 1, column=1, value="Vega charge by scope and scenario (USD)").font = Font(bold=True)
    vs = pd.read_sql("SELECT scope, scenario, vega_charge FROM res_vega_scenario WHERE basis='ALL'", conn)
    vs = vs.pivot_table(index="scope", columns="scenario", values="vega_charge").reset_index()
    vs = vs.reindex(columns=["scope", "high", "medium", "low"])
    _put(ws, vs, start_row=r0, money_cols=("high", "medium", "low"))
    r1 = r0 + len(vs) + 3
    opt = pd.read_sql("SELECT SUM(ABS(c.v_base)) AS g, SUM(c.v_base) AS n FROM curvature_cvr c JOIN positions p USING (position_id) "
                      "JOIN instruments i USING (instrument_id) WHERE i.has_optionality = 1", conn).iloc[0]
    firm_v = float(vs.loc[vs.scope == "FIRM", ["high", "medium", "low"]].max(axis=1).iloc[0])
    ws.cell(row=r1, column=1, value="Sanity: option book PV vs vega charge (USD)").font = Font(bold=True)
    for i, (lbl, val) in enumerate([("Gross |PV| of option positions", opt.g), ("Net PV of option positions", opt.n),
                                    ("Firm vega charge, worst scenario", firm_v),
                                    ("Vega charge / gross |PV|", firm_v / opt.g)], r1 + 1):
        ws.cell(row=i, column=1, value=lbl)
        ws.cell(row=i, column=2, value=float(val)).number_format = '0.00' if "/" in lbl else '#,##0'
    for i, line in enumerate([
            "Reading this:",
            "- For an at-the-money option, vega x implied vol equals the option price, and the weight is 100%, so a lone ATM option is charged its own PV.",
            "- Vega is charged on long AND short positions (the charge is a norm, not a P&L): short and long vega only net within the same risk factor / "
            "across factors with correlation < 100%.",
            "- Correlations between vega factors are close to 1 (alpha = 1%), so same-signed vega barely diversifies and the scenarios move the charge little "
            "(firm, US_RATES). Where long and short vega OFFSET across expiries (GLOBAL_RATES) lower correlation matters: high 0.7m vs low 2.8m, so 'low' is the worst scenario for vega.",
            "- The ES shocks vol with a modelled proxy (sheet SA_vs_ES): vol-only ES over 10 days, scaled by sqrt(6) for the 60-day horizon, is the closest counterpart to this charge."], r1 + 7):
        ws.cell(row=i, column=1, value=line)
    ws.column_dimensions["A"].width = 34
    for col in "BCDEF":
        ws.column_dimensions[col].width = 22

    # SA vs ES
    ws = wb.create_sheet("SA_vs_ES")
    cc = comp[comp.basis == "ALL"][["scope", "risk_class", "binding_scenario", "sa_charge", "es10_current",
                                    "es10_stressed", "es_current_lh_scaled", "es_stressed_lh_scaled",
                                    "es_stressed_lh_scaled_volfixed", "es_x1.5_indicative",
                                    "sa_over_es_stressed", "sa_over_es_x1.5", "sa_over_es_current",
                                    "sa_over_es_volfixed"]]
    cc = cc.sort_values(["scope", "risk_class"]).reset_index(drop=True)
    _put(ws, cc, money_cols=("sa_charge", "es10_current", "es10_stressed", "es_current_lh_scaled",
                             "es_stressed_lh_scaled", "es_stressed_lh_scaled_volfixed", "es_x1.5_indicative"),
         ratio_cols=("sa_over_es_stressed", "sa_over_es_x1.5", "sa_over_es_current", "sa_over_es_volfixed")); _style(ws)
    ws.add_image(XLImage(str(charts["saes"])), "Q2")
    ev = pd.read_sql("SELECT scope, measure, value FROM res_es WHERE measure LIKE 'ALL:VOL:%'", conn)
    ev = ev[ev.measure.isin(["ALL:VOL:es10_stressed", "ALL:VOL:es_stressed_lh_scaled"])]
    ev = ev.pivot(index="scope", columns="measure", values="value").reset_index()
    ev.columns = ["scope", "vol_only_es_10d_stressed", "vol_only_es_lh_scaled_60d"]
    sv = pd.read_sql("SELECT scope, charge AS sa_vega FROM res_sbm_components WHERE basis='ALL' AND "
                     "risk_class='TOTAL' AND component='vega'", conn)
    ev = ev.merge(sv, on="scope", how="left").fillna(0.0)
    ws.cell(row=len(cc) + 3, column=1, value="Vol risk alone: ES (modelled vol proxy) vs SA vega charge (USD)").font = Font(bold=True)
    _put(ws, ev, start_row=len(cc) + 4, money_cols=("vol_only_es_10d_stressed", "vol_only_es_lh_scaled_60d", "sa_vega"))
    r0 = len(cc) + 4 + len(ev) + 3
    for i, line in enumerate(note.splitlines()):
        ws.cell(row=r0 + i, column=1, value=line)
    ws.cell(row=r0, column=1).font = Font(bold=True)

    # Sensitivity ladder
    ws = wb.create_sheet("GIRR_ladder")
    lad = pd.read_sql("SELECT bucket AS currency, tenor_years, net_sensitivity/10000.0 AS net_pv01_usd_per_bp, "
                      "risk_weight, weighted_sensitivity FROM res_weighted_sens WHERE basis='ALL' AND scope='FIRM' "
                      "AND risk_class='GIRR' ORDER BY bucket, tenor_years", conn)
    _put(ws, lad, money_cols=("net_pv01_usd_per_bp", "weighted_sensitivity"), pct_cols=("risk_weight",)); _style(ws)
    fxl = pd.read_sql("SELECT bucket AS currency, net_sensitivity AS fx_delta_usd, risk_weight, weighted_sensitivity "
                      "FROM res_weighted_sens WHERE basis='ALL' AND scope='FIRM' AND risk_class='FX'", conn)
    _put(ws, fxl, start_row=len(lad) + 4, money_cols=("fx_delta_usd", "weighted_sensitivity"), pct_cols=("risk_weight",))

    # Parameters
    ws = wb.create_sheet("Parameters")
    pr = pd.read_sql("SELECT param_name, value, unit, source_ref, verified_in_text, note FROM sbm_parameters", conn)
    _put(ws, pr); _style(ws, widths={6: 70})
    r0 = len(pr) + 3
    ws.cell(row=r0, column=1, value="GIRR delta risk weights (MAR21.42 Table 1)").font = Font(bold=True)
    rw = pd.read_sql("SELECT tenor_years, risk_weight, source_ref FROM girr_tenor_rw", conn)
    _put(ws, rw, start_row=r0 + 1, pct_cols=("risk_weight",))
    ws.cell(row=r0 + len(rw) + 3, column=1, value="Tenor correlations: see table girr_tenor_corr (MAR21.46 Table 2).")

    # Notes
    ws = wb.create_sheet("Caveats")
    lines = [DISCLAIMER, "",
             "Parameters taken from the BCBS final standard d457 (Jan 2019) text and cross-checked; see Parameters sheet.",
             "Swaptions: Bachelier model at a flat ILLUSTRATIVE 90bp normal vol. GIRR vega is in the SA charge; the ES shocks vol with a MODELLED realised-vol proxy (no implied-vol history exists in the free data), 60-day horizon.",
             "Curvature: GIRR only, required scope = instruments with optionality; the MAR21.2(4) all-instrument variant is shown separately.",
             "Unverified / judgement items: the sqrt(2) risk-weight relief (MAR21.44, MAR21.88) is a bank option - "
             "switched OFF here (conservative). Government bonds would also carry credit-spread risk in a real SA - not modelled.",
             "Market data: USD yields and EURUSD/GBPUSD/USDCHF are FRED; EUR/GBP/CHF curves and their rate histories are ILLUSTRATIVE/MODELLED.",
             "Pricing: single-curve, annual coupons, no accrued interest or day counts; CMT par yields used as zero rates.",
             "Positions, books, desks, limits are all synthetic."]
    for i, line in enumerate(lines, 1):
        ws.cell(row=i, column=1, value=line)
    ws.column_dimensions["A"].width = 140

    wb.save(path)
    return path, comp
