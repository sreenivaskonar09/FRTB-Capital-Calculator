-- FRTB SBM educational calculator: schema (SQLite)
-- All portfolio data is SYNTHETIC. Parameters carry a source_ref to BCBS d457 (Jan 2019).
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- org structure
CREATE TABLE desks (
    desk_id      INTEGER PRIMARY KEY,
    desk_name    TEXT NOT NULL UNIQUE,
    description  TEXT
);

CREATE TABLE books (
    book_id               INTEGER PRIMARY KEY,
    desk_id               INTEGER NOT NULL REFERENCES desks(desk_id),
    book_name             TEXT NOT NULL UNIQUE,
    stated_purpose        TEXT NOT NULL CHECK (stated_purpose IN
        ('market_making','short_term_resale','arbitrage','hedge_of_trading','hold_to_maturity','alm_accrual')),
    accounting_class      TEXT NOT NULL CHECK (accounting_class IN ('FVTPL_TRADING','FVOCI','AMORTISED_COST')),
    expected_holding_days INTEGER NOT NULL,
    legal_impediment      INTEGER NOT NULL DEFAULT 0,   -- RBC25.3
    trading_book_flag     INTEGER,                      -- filled by 10_book_classification.sql
    classification_rule   TEXT,                         -- paragraph that drove the flag
    review_note           TEXT
);
CREATE INDEX ix_books_desk ON books(desk_id);

-- ---------------------------------------------------------------- instruments / positions
CREATE TABLE instruments (
    instrument_id   INTEGER PRIMARY KEY,
    instr_type      TEXT NOT NULL CHECK (instr_type IN ('BOND','IRS','FXFWD','SWAPTION')),
    description     TEXT,
    currency        TEXT NOT NULL,        -- currency of the instrument (foreign currency for FXFWD)
    notional        REAL NOT NULL,        -- face / swap notional / foreign-currency notional
    rate            REAL,                 -- bond coupon, IRS fixed rate, FXFWD strike (USD per 1 foreign), swaption strike
    maturity_years  REAL NOT NULL,        -- maturity; for a SWAPTION the option expiry
    underlying_tenor REAL,                -- SWAPTION: tenor of the underlying swap (years)
    normal_vol      REAL,                 -- SWAPTION: Bachelier (normal) vol, decimal per sqrt(year) - ILLUSTRATIVE
    option_type     TEXT CHECK (option_type IN ('PAYER','RECEIVER') OR option_type IS NULL),
    has_optionality INTEGER NOT NULL DEFAULT 0    -- MAR21.2(1)-(3): only these need curvature (and vega)
);

CREATE TABLE positions (
    position_id   INTEGER PRIMARY KEY,
    book_id       INTEGER NOT NULL REFERENCES books(book_id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    quantity      REAL NOT NULL,          -- +1 long / receive-fixed / buy foreign; -1 short / pay-fixed / sell foreign
    trade_date    TEXT NOT NULL
);
CREATE INDEX ix_pos_book ON positions(book_id);
CREATE INDEX ix_pos_instr ON positions(instrument_id);

-- ---------------------------------------------------------------- market data
CREATE TABLE curves (
    ccy          TEXT NOT NULL,
    tenor_years  REAL NOT NULL,
    zero_rate    REAL NOT NULL,           -- continuously compounded, decimal
    source       TEXT NOT NULL,           -- 'FRED DGSx 2026-10-13' or 'ILLUSTRATIVE'
    PRIMARY KEY (ccy, tenor_years)
);
CREATE TABLE fx_spots (
    ccy          TEXT PRIMARY KEY,        -- USD per 1 unit of ccy
    usd_per_unit REAL NOT NULL,
    source       TEXT NOT NULL
);

-- ---------------------------------------------------------------- parameters (all with a citation)
CREATE TABLE sbm_parameters (
    param_name       TEXT PRIMARY KEY,
    value            REAL NOT NULL,
    unit             TEXT,
    source_ref       TEXT NOT NULL,       -- BCBS d457 paragraph
    verified_in_text TEXT NOT NULL,       -- 'Y' if the phrase was found in the extracted standard
    note             TEXT
);
CREATE TABLE girr_tenor_rw (
    tenor_years REAL PRIMARY KEY,
    risk_weight REAL NOT NULL,
    source_ref  TEXT NOT NULL
);
CREATE TABLE girr_tenor_corr (
    tenor_k REAL NOT NULL,
    tenor_l REAL NOT NULL,
    rho     REAL NOT NULL,
    source_ref TEXT NOT NULL,
    PRIMARY KEY (tenor_k, tenor_l)
);
CREATE TABLE specified_currencies (
    currency                 TEXT PRIMARY KEY,
    girr_specified           INTEGER NOT NULL,
    fx_pair_vs_usd_specified INTEGER NOT NULL,
    source_ref               TEXT NOT NULL
);
CREATE TABLE girr_vega_tenors (
    tenor_years REAL PRIMARY KEY, source_ref TEXT NOT NULL
);
CREATE TABLE liquidity_horizons (
    j INTEGER PRIMARY KEY, lh_days INTEGER NOT NULL, source_ref TEXT NOT NULL
);

-- ---------------------------------------------------------------- risk factors, sensitivities
CREATE TABLE risk_factors (
    risk_factor TEXT PRIMARY KEY,         -- e.g. EUR_GIRR_5Y, EURUSD
    risk_class  TEXT NOT NULL CHECK (risk_class IN ('GIRR','FX')),
    bucket      TEXT NOT NULL,            -- GIRR: currency; FX: currency vs reporting ccy
    tenor_years REAL                      -- NULL for FX
);

CREATE TABLE sensitivities (
    position_id  INTEGER NOT NULL REFERENCES positions(position_id),
    risk_class   TEXT NOT NULL,
    bucket       TEXT NOT NULL,
    risk_factor  TEXT NOT NULL REFERENCES risk_factors(risk_factor),
    tenor_years  REAL,
    sensitivity  REAL NOT NULL,           -- MAR21.19 / 21.24 definition, reporting ccy (USD)
    pv01_usd     REAL,                    -- GIRR only: value change per +1bp (= sensitivity * 0.0001)
    PRIMARY KEY (position_id, risk_factor)
);
CREATE INDEX ix_sens_rf ON sensitivities(risk_factor);
CREATE INDEX ix_sens_class_bucket ON sensitivities(risk_class, bucket);

-- GIRR curvature inputs, one row per position x currency curve (MAR21.5, MAR21.99):
--   cvr_up = -(V(curve + RW) - V - RW*s)   cvr_dn = -(V(curve - RW) - V + RW*s)
--   s = sum of the position's GIRR delta sensitivities over all tenors of that curve.
CREATE TABLE curvature_cvr (
    position_id  INTEGER NOT NULL REFERENCES positions(position_id),
    bucket       TEXT NOT NULL,           -- currency
    risk_factor  TEXT NOT NULL,           -- <ccy>_GIRR_CURV
    shock        REAL NOT NULL,           -- parallel shift applied (decimal)
    s_sum        REAL NOT NULL,
    v_base       REAL NOT NULL,
    v_up         REAL NOT NULL,
    v_dn         REAL NOT NULL,
    cvr_up       REAL NOT NULL,
    cvr_dn       REAL NOT NULL,
    PRIMARY KEY (position_id, risk_factor)
);
CREATE INDEX ix_curv_bucket ON curvature_cvr(bucket);

-- GIRR vega inputs, one row per position x vega risk factor (option maturity x underlying maturity), MAR21.25:
--   sensitivity = alloc_weight * vega * implied_vol,  vega = dV/d(sigma) in USD.  The (normal-vol) swaption is mapped
--   to the 0.5/1/3/5/10y grid by bilinear interpolation (an assumption: MAR21.8(4) says "mapped to one or several").
CREATE TABLE vega_sensitivities (
    position_id      INTEGER NOT NULL REFERENCES positions(position_id),
    bucket           TEXT NOT NULL,        -- currency
    risk_factor      TEXT NOT NULL,        -- <ccy>_GIRR_VEGA_<optionT>Y_<underlyingT>Y
    option_tenor     REAL NOT NULL,
    underlying_tenor REAL NOT NULL,
    alloc_weight     REAL NOT NULL,
    vega             REAL NOT NULL,        -- dV/d(normal vol), USD per 1.00 of vol (not per bp)
    implied_vol      REAL NOT NULL,
    sensitivity      REAL NOT NULL,
    PRIMARY KEY (position_id, risk_factor)
);
CREATE INDEX ix_vega_bucket ON vega_sensitivities(bucket);

CREATE TABLE limits (
    desk_id      INTEGER NOT NULL REFERENCES desks(desk_id),
    risk_class   TEXT NOT NULL,
    limit_amount REAL NOT NULL,           -- synthetic limit, USD of SBM delta capital
    PRIMARY KEY (desk_id, risk_class)
);

-- ---------------------------------------------------------------- result tables (one per calculation step)
-- basis: 'ALL' = every position as booked; 'TRADING' = banking-book GIRR excluded (FX kept, MAR12.6)
-- scope: 'FIRM' or the desk_name (each desk capitalised standalone, MAR21.7(2)(b))
CREATE TABLE res_net_sens (
    basis TEXT NOT NULL, scope TEXT NOT NULL, risk_class TEXT NOT NULL, bucket TEXT NOT NULL,
    risk_factor TEXT NOT NULL, tenor_years REAL, net_sensitivity REAL NOT NULL, n_positions INTEGER NOT NULL,
    PRIMARY KEY (basis, scope, risk_factor)
);
CREATE TABLE res_weighted_sens (
    basis TEXT NOT NULL, scope TEXT NOT NULL, risk_class TEXT NOT NULL, bucket TEXT NOT NULL,
    risk_factor TEXT NOT NULL, tenor_years REAL, net_sensitivity REAL NOT NULL,
    risk_weight REAL NOT NULL, weighted_sensitivity REAL NOT NULL,
    PRIMARY KEY (basis, scope, risk_factor)
);
CREATE TABLE res_bucket_kb (
    basis TEXT NOT NULL, scope TEXT NOT NULL, scenario TEXT NOT NULL, risk_class TEXT NOT NULL,
    bucket TEXT NOT NULL, kb REAL NOT NULL, sb_raw REAL NOT NULL, sb_used REAL NOT NULL,
    PRIMARY KEY (basis, scope, scenario, risk_class, bucket)
);
CREATE TABLE res_delta_scenario (
    basis TEXT NOT NULL, scope TEXT NOT NULL, scenario TEXT NOT NULL, risk_class TEXT NOT NULL,
    delta_charge REAL NOT NULL, used_alt_sb INTEGER NOT NULL,
    PRIMARY KEY (basis, scope, scenario, risk_class)
);
CREATE TABLE res_sbm_final (
    basis TEXT NOT NULL, scope TEXT NOT NULL, risk_class TEXT NOT NULL,   -- risk_class 'TOTAL' = sum over classes
    binding_scenario TEXT NOT NULL,       -- scenario with the largest TOTAL for this scope
    charge_at_binding REAL NOT NULL,
    PRIMARY KEY (basis, scope, risk_class)
);
-- Curvature (GIRR). cmode: 'OPT' = instruments with optionality only (MAR21.2(1)-(3), the required scope);
-- 'ALL' = holistic variant allowed by MAR21.2(4) (all instruments), reported as a sensitivity analysis only.
CREATE TABLE res_net_curvature (
    basis TEXT NOT NULL, scope TEXT NOT NULL, cmode TEXT NOT NULL, bucket TEXT NOT NULL,
    cvr_up REAL NOT NULL, cvr_dn REAL NOT NULL, n_positions INTEGER NOT NULL,
    PRIMARY KEY (basis, scope, cmode, bucket)
);
CREATE TABLE res_curv_bucket (
    basis TEXT NOT NULL, scope TEXT NOT NULL, cmode TEXT NOT NULL, scenario TEXT NOT NULL, bucket TEXT NOT NULL,
    k_up REAL NOT NULL, k_dn REAL NOT NULL, selected TEXT NOT NULL, kb REAL NOT NULL, sb REAL NOT NULL,
    PRIMARY KEY (basis, scope, cmode, scenario, bucket)
);
CREATE TABLE res_curvature_scenario (
    basis TEXT NOT NULL, scope TEXT NOT NULL, cmode TEXT NOT NULL, scenario TEXT NOT NULL, risk_class TEXT NOT NULL,
    curvature_charge REAL NOT NULL,
    PRIMARY KEY (basis, scope, cmode, scenario, risk_class)
);
-- delta / curvature split of the charge at each scope's binding scenario (cmode OPT)
CREATE TABLE res_sbm_components (
    basis TEXT NOT NULL, scope TEXT NOT NULL, risk_class TEXT NOT NULL, component TEXT NOT NULL,
    binding_scenario TEXT NOT NULL, charge REAL NOT NULL,
    PRIMARY KEY (basis, scope, risk_class, component)
);
-- GIRR vega (MAR21.4 applied to vega risk factors, correlations MAR21.93, gamma MAR21.95)
CREATE TABLE res_net_vega (
    basis TEXT NOT NULL, scope TEXT NOT NULL, bucket TEXT NOT NULL, risk_factor TEXT NOT NULL,
    option_tenor REAL NOT NULL, underlying_tenor REAL NOT NULL, net_sensitivity REAL NOT NULL, n_positions INTEGER NOT NULL,
    PRIMARY KEY (basis, scope, risk_factor)
);
CREATE TABLE res_weighted_vega (
    basis TEXT NOT NULL, scope TEXT NOT NULL, bucket TEXT NOT NULL, risk_factor TEXT NOT NULL,
    option_tenor REAL NOT NULL, underlying_tenor REAL NOT NULL, net_sensitivity REAL NOT NULL,
    risk_weight REAL NOT NULL, weighted_sensitivity REAL NOT NULL,
    PRIMARY KEY (basis, scope, risk_factor)
);
CREATE TABLE res_vega_bucket (
    basis TEXT NOT NULL, scope TEXT NOT NULL, scenario TEXT NOT NULL, bucket TEXT NOT NULL,
    kb REAL NOT NULL, sb_raw REAL NOT NULL, sb_used REAL NOT NULL,
    PRIMARY KEY (basis, scope, scenario, bucket)
);
CREATE TABLE res_vega_scenario (
    basis TEXT NOT NULL, scope TEXT NOT NULL, scenario TEXT NOT NULL, risk_class TEXT NOT NULL,
    vega_charge REAL NOT NULL, used_alt_sb INTEGER NOT NULL,
    PRIMARY KEY (basis, scope, scenario, risk_class)
);
CREATE TABLE res_limit_util (
    basis TEXT NOT NULL, desk_name TEXT NOT NULL, risk_class TEXT NOT NULL, limit_amount REAL NOT NULL,
    charge REAL NOT NULL, utilisation REAL NOT NULL, breach INTEGER NOT NULL,
    PRIMARY KEY (basis, desk_name, risk_class)
);
CREATE TABLE res_es (
    scope TEXT NOT NULL, measure TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (scope, measure)
);
