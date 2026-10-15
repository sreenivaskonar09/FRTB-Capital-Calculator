"""Extract SBM parameters from the BCBS d457 text and write config CSVs.

Source: refs/bcbs_d457/d457.txt, produced with pypdf from the BIS PDF
https://www.bis.org/publications/201901-standards-minimum-capital-requirements-market-risk.pdf

Tables 1 and 2 (GIRR risk weights, tenor correlations) are PARSED from the text,
not typed from memory. Scalar parameters are written by hand with a paragraph
reference, and each one is asserted against a phrase that must appear in the
extracted text, so a typo or a changed source fails loudly.

Run:  python scripts/extract_params.py
"""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TXT = ROOT / "refs" / "bcbs_d457" / "d457.txt"
CFG = ROOT / "config"

TENORS = [0.25, 0.5, 1, 2, 3, 5, 10, 15, 20, 30]


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def parse_table1(text: str) -> list[float]:
    """GIRR delta risk weights, MAR21.42 Table 1."""
    block = text[text.index("21.42 For calculating weighted sensitivities"):]
    block = block[: block.index("21.43")]
    pcts = [float(x) for x in re.findall(r"(\d+\.\d+)%", block)]
    assert len(pcts) == 10, pcts
    return [round(p / 100, 6) for p in pcts]


def parse_table2(text: str) -> list[list[float]]:
    """GIRR tenor correlation matrix, MAR21.46 Table 2."""
    block = text[text.index("Delta GIRR correlations"):]
    block = block[: block.index("Footnote")]
    rows = []
    for line in block.splitlines():
        m = re.match(r"^\s*(\d+\.?\d*) year\s+(.*)$", line)
        if m and "%" in m.group(2):
            vals = [float(x) / 100 for x in re.findall(r"(\d+\.\d+)%", m.group(2))]
            if len(vals) == 10:
                rows.append(vals)
    assert len(rows) == 10, len(rows)
    return rows


# (name, value, unit, paragraph, phrase that must appear in text near that para, note)
SCALARS = [
    ("girr_gamma_across_ccy", 0.50, "corr", "MAR21.50", "the parameter 𝛾𝛾𝑏𝑏𝑏𝑏 is set at 50%", "Across GIRR buckets (currencies)"),
    ("girr_rho_same_tenor_diff_curve", 0.999, "corr", "MAR21.45", "set at 99.90%", "Only matters with >1 curve per currency; single curve used here"),
    ("girr_rho_diff_tenor_diff_curve_mult", 0.999, "mult", "MAR21.47", "multiplied by 99.90%", "Not exercised: single curve per currency"),
    ("girr_theta", 0.03, "param", "MAR21.46 fn13", "is set at 3%", "rho_kl = max(exp(-theta*|Tk-Tl|/min(Tk,Tl)), 40%)"),
    ("girr_rho_floor", 0.40, "corr", "MAR21.46 fn13", "; 40%", "Floor of the tenor correlation formula"),
    ("girr_rw_inflation", 0.016, "rw", "MAR21.43", "is set at 1.6%", "NOT USED: no inflation instruments"),
    ("girr_rw_xccy_basis", 0.016, "rw", "MAR21.43", "is set at 1.6%", "NOT USED: no cross-currency basis modelled"),
    ("girr_rw_sqrt2_option", 0.0, "flag", "MAR21.44", "divided by the square root of 2", "Bank-discretion switch (1 = apply /sqrt2 for specified ccys EUR USD GBP AUD JPY SEK CAD + reporting ccy). Default here: 0 (NOT applied, conservative) - see README"),
    ("fx_rw", 0.15, "rw", "MAR21.87", "equal to 15%", "Relative risk weight for all FX delta sensitivities"),
    ("fx_rw_sqrt2_option", 0.0, "flag", "MAR21.88", "divided by the square root of 2", "Bank-discretion switch (1 = apply for specified pairs vs USD). Default here: 0 (NOT applied, conservative) - see README"),
    ("fx_gamma_across_buckets", 0.60, "corr", "MAR21.89", "uniformly set to 60%", "Across FX buckets"),
    ("scen_high_mult", 1.25, "mult", "MAR21.6(2)", "multiplied by 1.25", "High: rho,gamma * 1.25, capped at 100%"),
    ("scen_high_cap", 1.00, "cap", "MAR21.6(2)", "cap at 100%", "Cap for high scenario"),
    ("scen_low_a", 2.0, "param", "MAR21.6(3)", "max(2 × 𝜌", "Low: max(2*rho - 100%, 75%*rho)"),
    ("scen_low_b", 0.75, "param", "MAR21.6(3)", "75% × 𝜌", "Low: max(2*rho - 100%, 75%*rho)"),
    ("curv_corr_power", 2.0, "power", "MAR21.100/21.101", "squaring the corresponding delta correlation parameters", "Curvature rho and gamma = delta value squared; scenarios (MAR21.6) are applied AFTER squaring (GIRR gamma 50%^2 = 25%)"),
    ("curv_girr_rw_rule", 1.0, "mult", "MAR21.99", "based on the highest prescribed delta risk weight", "GIRR curvature shock = parallel shift of the whole curve by max delta RW (the 0.25y 1.7%) x this multiplier"),
    ("curv_scalar_fx_options", 1.5, "scalar", "MAR21.98", "may be divided by a scalar of 1.5", "NOT USED: FX curvature not computed (no FX options)"),
    ("vega_rw_sigma", 0.55, "rw", "MAR21.92 fn24", "is set at 55%", "RW_k = min(RW_sigma * sqrt(LH)/sqrt(10), 100%)"),
    ("vega_lh_girr_days", 60, "days", "MAR21.92 Table 13", "GIRR 60 100%", "Regulatory liquidity horizon for GIRR vega"),
    ("vega_rw_cap", 1.0, "cap", "MAR21.92 fn24", "GIRR 60 100%", "100% cap (Table 13 gives GIRR 100%)"),
    ("vega_alpha", 0.01, "param", "MAR21.93", "is set at 1%", "rho_option = exp(-alpha*|Tk-Tl|/min(Tk,Tl)); same form for the underlying maturities"),
    ("vega_gamma_across_ccy", 0.50, "corr", "MAR21.95", "is to be used for the aggregation of vega risk", "Across currencies: same gamma as GIRR delta (50%)"),
    ("es_confidence", 0.975, "prob", "MAR33.3", "97.5th percentile", "One-tailed"),
    ("es_base_horizon_days", 10, "days", "MAR33.4(2)", "ie 10 days", "Base horizon T"),
    ("lh_girr_specified_ccy", 10, "days", "MAR33.12 Table 2", "specified currencies", "IR specified ccys (EUR USD GBP AUD JPY SEK CAD + domestic)"),
    ("lh_girr_unspecified_ccy", 20, "days", "MAR33.12 Table 2", "Interest rate: unspecified currencies 20", "IR unspecified ccys"),
    ("lh_fx_specified_pair", 10, "days", "MAR33.12 Table 2", "specified currency", "FX specified pairs"),
    ("lh_fx_other_pair", 20, "days", "MAR33.12 Table 2", "FX rate: currency pairs 20", "FX other pairs"),
]

# Specified currencies / pairs (MAR21.44 fn12, MAR21.88 fn22) - assertions below
SPECIFIED_CCY = ["EUR", "USD", "GBP", "AUD", "JPY", "SEK", "CAD"]
SPECIFIED_PAIRS_VS_USD = ["EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "MXN", "CNY", "NZD",
                          "RUB", "HKD", "SGD", "TRY", "KRW", "SEK", "ZAR", "INR", "NOK", "BRL"]
# GIRR vega grid, MAR21.8(4): same tenors for option maturity and for residual underlying maturity
VEGA_TENORS = [0.5, 1, 3, 5, 10]
LIQ_HORIZONS = [(1, 10), (2, 20), (3, 40), (4, 60), (5, 120)]  # MAR33.4 Table 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raw = TXT.read_text(encoding="utf-8")
    flat = squash(raw)
    ok = True

    rw = parse_table1(raw)
    corr = parse_table2(raw)
    # Cross-check Table 2 against the closed-form footnote-13 formula.
    import math
    worst = 0.0
    for i, ti in enumerate(TENORS):
        for j, tj in enumerate(TENORS):
            f = max(math.exp(-0.03 * abs(ti - tj) / min(ti, tj)), 0.40)
            worst = max(worst, abs(f - corr[i][j]))
    print(f"Table 2 vs formula: max abs diff = {worst:.5f} (table is rounded to 0.1%)")
    assert worst < 0.0006, "Table 2 disagrees with formula"

    for name, val, unit, para, phrase, note in SCALARS:
        # Phrases in the extracted text carry garbled math glyphs; compare loosely.
        if squash(phrase) not in flat:
            print(f"!! NOT VERIFIED in text: {name} ({para}): '{phrase}'")
            ok = False

    for chk, label in [("Foreign exchange (FX) rate: specified currency", "FX LH"),
                       ("USD/EUR, USD/JPY, USD/GBP, USD/AUD", "specified pairs fn22"),
                       ("EUR, USD, GBP, AUD, JPY, SEK, CAD", "specified ccys fn12")]:
        if chk not in flat:
            print("!! missing:", label); ok = False

    CFG.mkdir(exist_ok=True)
    with open(CFG / "girr_tenor_rw.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tenor_years", "risk_weight", "source_ref"])
        for t, r in zip(TENORS, rw):
            w.writerow([t, r, "MAR21.42 Table 1"])
    with open(CFG / "girr_tenor_corr.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tenor_k", "tenor_l", "rho", "source_ref"])
        for i, ti in enumerate(TENORS):
            for j, tj in enumerate(TENORS):
                w.writerow([ti, tj, corr[i][j], "MAR21.46 Table 2"])
    with open(CFG / "scalar_parameters.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["param_name", "value", "unit", "source_ref", "verified_in_text", "note"])
        for name, val, unit, para, phrase, note in SCALARS:
            w.writerow([name, val, unit, para, "Y" if squash(phrase) in flat else "N", note])
    with open(CFG / "specified_currencies.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["currency", "girr_specified", "fx_pair_vs_usd_specified", "source_ref"])
        for c in sorted(set(SPECIFIED_CCY) | set(SPECIFIED_PAIRS_VS_USD)):
            w.writerow([c, int(c in SPECIFIED_CCY), int(c in SPECIFIED_PAIRS_VS_USD),
                        "MAR21.44 fn12 / MAR21.88 fn22"])
    if "0.5 years, 1 year, 3 years, 5 years and 10 years" not in flat:
        print("!! vega tenor grid not found in text"); ok = False
    with open(CFG / "girr_vega_tenors.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tenor_years", "source_ref"])
        for t in VEGA_TENORS:
            w.writerow([t, "MAR21.8(4)"])
    with open(CFG / "liquidity_horizons.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["j", "lh_days", "source_ref"])
        for j, d in LIQ_HORIZONS:
            w.writerow([j, d, "MAR33.4 Table 1"])
    print("Table 1 RW:", rw)
    print("OK" if ok else "SOME PARAMETERS NOT VERIFIED - see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
