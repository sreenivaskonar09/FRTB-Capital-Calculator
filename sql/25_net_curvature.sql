-- GIRR curvature, step 2 (MAR21.5(2)): CVR+ / CVR- summed over positions per currency curve, per scope.
-- :basis = 'ALL' | 'TRADING' (banking-book positions excluded; banking-book FX is untouched, no FX curvature here)
-- :cmode = 'OPT' instruments with optionality only (MAR21.2(1)-(3), the required scope)
--          'ALL' every instrument, the holistic variant MAR21.2(4) permits (sensitivity analysis, not headline)
INSERT INTO res_net_curvature (basis, scope, cmode, bucket, cvr_up, cvr_dn, n_positions)
WITH base AS (
    SELECT d.desk_name, c.bucket, c.cvr_up, c.cvr_dn
    FROM curvature_cvr c
    JOIN positions   p ON p.position_id   = c.position_id
    JOIN instruments i ON i.instrument_id = p.instrument_id
    JOIN books       b ON b.book_id       = p.book_id
    JOIN desks       d ON d.desk_id       = b.desk_id
    WHERE (:basis = 'ALL' OR b.trading_book_flag = 1)
      AND (:cmode = 'ALL' OR i.has_optionality = 1)
)
SELECT :basis, 'FIRM', :cmode, bucket, SUM(cvr_up), SUM(cvr_dn), COUNT(*) FROM base GROUP BY bucket
UNION ALL
SELECT :basis, desk_name, :cmode, bucket, SUM(cvr_up), SUM(cvr_dn), COUNT(*) FROM base GROUP BY desk_name, bucket;
