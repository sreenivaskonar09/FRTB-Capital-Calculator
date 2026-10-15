-- GIRR vega, step 2 (MAR21.4(2) applied to vega risk factors): net the vega sensitivities by risk factor
-- (currency x option maturity x underlying maturity), per scope.
-- :basis = 'ALL' | 'TRADING' (banking-book positions excluded)
INSERT INTO res_net_vega (basis, scope, bucket, risk_factor, option_tenor, underlying_tenor, net_sensitivity, n_positions)
WITH base AS (
    SELECT d.desk_name, v.bucket, v.risk_factor, v.option_tenor, v.underlying_tenor, v.sensitivity
    FROM vega_sensitivities v
    JOIN positions p ON p.position_id = v.position_id
    JOIN books     b ON b.book_id     = p.book_id
    JOIN desks     d ON d.desk_id     = b.desk_id
    WHERE :basis = 'ALL' OR b.trading_book_flag = 1
)
SELECT :basis, 'FIRM', bucket, risk_factor, option_tenor, underlying_tenor, SUM(sensitivity), COUNT(*)
FROM base GROUP BY bucket, risk_factor, option_tenor, underlying_tenor
UNION ALL
SELECT :basis, desk_name, bucket, risk_factor, option_tenor, underlying_tenor, SUM(sensitivity), COUNT(*)
FROM base GROUP BY desk_name, bucket, risk_factor, option_tenor, underlying_tenor;
