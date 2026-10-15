# FRTB Standardised Approach (SBM: GIRR delta, vega, curvature; FX delta) calculator: educational, synthetic data

An educational implementation of the Basel FRTB **Sensitivities-Based Method** (GIRR delta, GIRR vega, GIRR curvature, FX delta) with a SQLite
backend, limit monitoring, and a partial comparison to a 97.5% **Expected Shortfall** (IMA-style) measure on the same
portfolio. **All positions, books, desks and limits are synthetic.** Not production-grade, not regulatory-compliant.

## What it does
1. Generates ~195 synthetic positions (govt bonds, IRS, FX forwards, swaptions; USD/EUR/GBP/CHF; 4 desks, 11 books) and computes
   bump-and-revalue sensitivities (GIRR PV01 per MAR21.19 by tenor vertex; FX delta per MAR21.24) into SQL.
   Swaptions (Bachelier, flat illustrative 90bp normal vol) are the instruments with optionality that curvature applies to.
2. SQL nets by risk factor and applies risk weights (`sql/20_*`, `sql/30_*`); Python does the quadratic forms
   (intra-bucket Kb, inter-bucket gamma incl. the MAR21.4(5)(b) alternative) under **high / medium / low** correlations; SQL picks
   the binding scenario (`sql/50_*`). Results by risk class, desk (each standalone, MAR21.7(2)(b)) and firm.
3. Limit utilisation and breach flags (`sql/60_*`), using synthetic limits.
4. Rules-based trading/banking flag (`sql/10_*`, RBC25) and the capital effect of excluding banking-book positions.
5. 97.5% ES by historical simulation (10-day overlapping, full revaluation, liquidity-horizon cascade, worst-12-month stress window) with
   rate, FX and swaption-vol shocks; vol is a modelled proxy (see below), and a vol-held-fixed variant is kept for comparison.
6. Excel workbook + charts + `output/SA_vs_ES_note.md`.

## What it does NOT do
FX vega and FX curvature (no FX options), CSR on the government bonds, equity/commodity, inflation and cross-currency-basis
risk factors, default risk charge, residual risk add-on, NMRF, IMCC risk-class aggregation, backtesting/PLA multipliers, desk
eligibility. So the SA-vs-ES comparison is **partial**, and "SA" here is GIRR delta + GIRR vega + GIRR curvature + FX delta only. **The ES shocks swaption vol with a MODELLED realised-vol proxy (no implied-vol history exists in the free data).**

## Run
```
pip install -r requirements.txt
python scripts/fetch_data.py   # one-off: downloads the public FRED yields and FX (not committed to the repo)
python run_all.py              # build DB -> SBM -> ES -> reconcile -> tests -> Excel + charts (~15 s)
```
Outputs: `data/frtb_sbm.db`, `output/FRTB_SBM_report.xlsx`, `output/*.png`, `output/SA_vs_ES_note.md`
(a pre-built copy of the report and charts is committed under `output/`).

Re-verify the parameters against the standard (optional; `config/*.csv` are committed):
```
python scripts/fetch_data.py --standard   # downloads the BCBS d457 PDF and extracts its text
python scripts/extract_params.py          # re-parses the tables and checks every scalar against the text
```

## Data sources and attribution
- **Parameters:** Basel Committee on Banking Supervision, *Minimum capital requirements for market risk* (d457, January 2019), bis.org. The
  standard itself is not included in this repo; `config/*.csv` hold the extracted numbers with paragraph references.
- **Market data:** FRED (Federal Reserve Bank of St. Louis): US Treasury constant-maturity yields (`DGS*`) and `DEXUSEU`, `DEXUSUK`, `DEXSZUS`.
  Downloaded on demand by `scripts/fetch_data.py`; check FRED's terms of use before redistributing the files.
- **Everything else is synthetic or modelled:** positions, books, desks, limits, EUR/GBP/CHF curves, non-USD rate histories, swaption vol and the vol proxy.

## Disclaimer
Educational project. Not production-grade, not regulatory-compliant, not investment or capital advice, and not a capital figure for any
real institution. Provided as-is under the MIT licence; not affiliated with the Basel Committee, the BIS or any bank.

## Headline results (this synthetic book, as of 2026-10-13)
Firm SBM **USD 382.2m** = delta 357.1m + GIRR vega 21.1m + GIRR curvature 4.0m (binding scenario: high correlation) vs stressed
liquidity-adjusted ES **USD 108.0m**: SA = 3.5x ES (3.3x excluding vega; 2.4x if ES is multiplied by an indicative mc = 1.5).
Excluding banking-book GIRR: USD 327.2m (-14.4%); banking-book FX stays in scope (MAR12.6).

### Volatility in the ES (modelled proxy)
- Banks do not hold vol fixed: vol is a risk factor ("interest rate: volatility", 60-day liquidity horizon, MAR33.12) with its own history.
  No free implied-vol history exists, so this project shocks swaption vol with a **proxy**: the 10-day relative change in the 60-day realised
  normal vol of the real USD 5y yield, scaled so today's level is the 90bp used in pricing, applied as one flat relative shock (sticky strike,
  no smile, no expiry/tenor structure) to every swaption. It is an assumption, not market data.
- The liquidity-horizon cascade (MAR33.4) is applied with the table in SQL: vol (60d) enters the j=2,3,4 subsets, CHF rates (20d) the j=2 subset.
  Vol alone: USD 9.4m over 10 days, 23.0m after horizon scaling (x sqrt(6)), versus the SA vega charge of 21.1m.
- Effect on the whole book is small: ES 108.0m with vol shocks vs 108.8m held fixed (-0.8%); US_RATES +5.9% (net-long vega book).
  The firm total probably barely moves because net-long USD vega gains when the proxy vol rises in a rate stress (not separately verified).
- In a real IMA, vol points often fail the risk factor eligibility test (MAR31.13: 24 real price observations a year with no 90-day gap under 4,
  or 100 in 12 months) and become non-modellable with a separate stress-scenario charge. That is not modelled here.

### GIRR vega (MAR21.25, MAR21.8(4), MAR21.92-95)
- Sensitivity `s = vega x implied vol` (MAR21.25), with vega = dV/d(normal vol). Under a normal-vol model this equals the lognormal
  result (MAR21.28 fn9). For an at-the-money option `s` equals the option price, which the tests use as a check.
- Risk factors: currency x option maturity x residual underlying maturity on the 0.5/1/3/5/10y grid. Swaptions are mapped onto the grid by
  **bilinear interpolation** (assumption: the standard says "mapped to one or several tenors", not how). The allocation conserves total vega.
- Weight `min(55% x sqrt(60)/sqrt(10), 100%) = 100%` (the cap binds: 134.7% before it). Correlation
  `min(exp(-1% |dTe|/min Te) x exp(-1% |dTu|/min Tu), 1)`, gamma across currencies 50%; scenarios as for delta.
- Charged on long and short alike (it is a norm of the weighted vega vector, not a P&L). Same-signed vega barely diversifies, so scenarios
  move it little for the firm and US_RATES; for GLOBAL_RATES, where long and short vega offset across expiries, 'low' correlation is the worst
  case (0.7m high vs 2.8m low).
- Not modelled: FX vega (no FX options), inflation and cross-currency-basis vega, vol surface/smile (one flat illustrative 90bp vol).

### GIRR curvature (MAR21.5, MAR21.99-101)
- Shock = parallel shift of a whole currency curve by the highest delta weight (1.7%); `CVR = -(V(shocked) - V - RW*s)`, s = sum of the
  position's GIRR delta over all tenors. Each currency is one bucket and one risk factor, so within-bucket correlation never enters;
  across currencies gamma = 50%^2 = 25%, and the high/low scenarios are applied to that squared value, not before squaring.
- **Scope matters.** The standard requires curvature only for instruments with optionality (MAR21.2(1)-(3)); plain bonds, swaps and FX
  forwards are linear in their cash flows. So a book without options has zero required curvature, which is why swaptions were added.
  MAR21.2(4) lets a bank include all instruments if it does so consistently; that variant is reported separately and gives **zero** here,
  because the bond book is net long convexity. It is a sensitivity analysis, not the headline.
- Result: EUR options are net short gamma (curvature charge USD 13.5m at GLOBAL_RATES), USD options net long gamma (no charge). At firm
  level the USD long gamma offsets part of the EUR short gamma across currencies at 25%, so firm curvature (4.0m) is below the desk's.
  The 'high' scenario is not the worst scenario for curvature (low is), but binding is decided on delta + curvature together (MAR21.7).
Three of eight desk x risk-class limits breach (now measured on delta + curvature) (synthetic limits, set to demonstrate flagging).

## Parameters and sources
All from BCBS d457 (Jan 2019), extracted from the PDF text, not from memory. Every parameter row carries a paragraph reference
(`sbm_parameters.source_ref`; `config/*.csv`), and `verified_in_text = Y` means the supporting phrase was found in the extracted standard.
Table 1 (GIRR weights, MAR21.42) and Table 2 (tenor correlations, MAR21.46) are parsed from the text; Table 2 is cross-checked against the footnote-13
formula (max difference 0.05%, rounding). Gamma 50% GIRR (MAR21.50), FX weight 15% and gamma 60% (MAR21.87, .89), scenarios (MAR21.6), ES (MAR33.3, .4, .12).

**Assumptions / items to flag**
- sqrt(2) risk-weight relief for specified currencies (MAR21.44, .88) is a bank option; **switched off** (`config/scalar_parameters.csv`, rows `*_sqrt2_option`).
- Inflation / cross-currency-basis weights are extracted but unused (no such instruments).
- Trading-book flag follows RBC25; the 365-day "review" check is my heuristic, not a BCBS threshold. The standard sets no numeric holding period.
- Swaption vol is a flat illustrative 90bp (normal), not calibrated; swaption strikes are set around the forward swap rate. Vega is analytic (Bachelier).
- Pricing is simplified (single curve, annual coupons, no accrual/day count; CMT par yields used as zero rates; 15y interpolated).
- Data: USD yields and EURUSD/GBPUSD/USDCHF are real FRED series. **EUR/GBP/CHF curves are illustrative and their rate histories are modelled** (beta x USD changes + noise), so non-USD GIRR ES is assumption-driven.
- ES stress window: worst rolling 250 obs of 10-day overlapping changes since 2007 (MAR33.7 idea); no reduced-factor-set scaling (MAR33.6).
- ES vol shocks are a modelled realised-vol proxy, not implied vols (see above).
- FRED series were downloaded to `refs/fred` (includes DEXSZUS for CHF).

## Validation
- 79 pytest tests: hand-checked single position, offsetting positions, single bucket (all three scenarios), two-bucket gamma, FX, alternative-Sb branch,
  binding-scenario logic, sensitivity definitions, classification rules, ES arithmetic, parameter integrity; curvature: one-bucket, two-bucket with
  squared gamma in each scenario, psi sign rule, tie-break, options-only scope vs MAR21.2(4) variant, CVR definition and sign by instrument,
  Bachelier ATM price and payer-receiver parity; vega: single factor, offsetting vega, two expiries (all scenarios), both tenor dimensions,
  two currencies, ATM vega = option price, analytic vs finite-difference vega, bilinear mapping, linear instruments carry none;
  ES vol: proxy construction and scaling, liquidity-horizon cascade (incl. vol = sqrt(60/10)), ATM option P&L proportional to the vol shock,
  linear instruments unaffected, unit vol factors reproduce the vol-fixed ES.
- `frtb/reconcile.py`: independent pandas + explicit-loop recomputation (delta from the raw sensitivities and `config/*.csv`, curvature from the raw per-position CVRs, vega from the raw vega rows with its own weights and correlations); matches the SQL pipeline to < 1e-7 USD on both bases.

## Layout
`sql/` schema + calculation queries | `frtb/` modules (pricing, sensitivities, sbm, es, reconcile, report) | `config/` cited parameters | `tests/` | `scripts/` data fetch and parameter extraction | `refs/` downloaded inputs (git-ignored).
