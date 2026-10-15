-- Step 2 (MAR21.4(2)): net sensitivities by risk factor, per scope.
-- :basis = 'ALL'     every position as booked
--          'TRADING' banking-book positions excluded EXCEPT FX (MAR12.6: banking-book FX stays in scope)
-- Scope 'FIRM' = whole book; one scope per desk = desk capitalised standalone (MAR21.7(2)(b)).
INSERT INTO res_net_sens (basis, scope, risk_class, bucket, risk_factor, tenor_years, net_sensitivity, n_positions)
WITH base AS (
    SELECT d.desk_name, s.risk_class, s.bucket, s.risk_factor, s.tenor_years, s.sensitivity
    FROM sensitivities s
    JOIN positions p ON p.position_id = s.position_id
    JOIN books     b ON b.book_id     = p.book_id
    JOIN desks     d ON d.desk_id     = b.desk_id
    WHERE :basis = 'ALL' OR b.trading_book_flag = 1 OR s.risk_class = 'FX'
)
SELECT :basis, 'FIRM', risk_class, bucket, risk_factor, tenor_years, SUM(sensitivity), COUNT(*)
FROM base GROUP BY risk_class, bucket, risk_factor, tenor_years
UNION ALL
SELECT :basis, desk_name, risk_class, bucket, risk_factor, tenor_years, SUM(sensitivity), COUNT(*)
FROM base GROUP BY desk_name, risk_class, bucket, risk_factor, tenor_years;
