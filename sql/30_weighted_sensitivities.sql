-- Step 3 (MAR21.4(3)): WS_k = RW_k * s_k.  Risk weights come from the parameter tables.
--   GIRR: Table 1 weight by tenor (MAR21.42), optionally / sqrt(2) for specified currencies (MAR21.44)
--   FX  : 15% (MAR21.87), optionally / sqrt(2) for specified pairs (MAR21.88)
INSERT INTO res_weighted_sens
WITH rw AS (
    SELECT n.*,
           CASE n.risk_class
             WHEN 'GIRR' THEN w.risk_weight
                  * CASE WHEN g_opt.value = 1 AND sc.girr_specified = 1 THEN inv.value ELSE 1.0 END
             WHEN 'FX'   THEN fx_rw.value
                  * CASE WHEN fx_opt.value = 1 AND sc.fx_pair_vs_usd_specified = 1 THEN inv.value ELSE 1.0 END
           END AS risk_weight
    FROM res_net_sens n
    LEFT JOIN girr_tenor_rw w ON n.risk_class = 'GIRR' AND w.tenor_years = n.tenor_years
    LEFT JOIN specified_currencies sc ON sc.currency = n.bucket
    JOIN sbm_parameters g_opt  ON g_opt.param_name  = 'girr_rw_sqrt2_option'
    JOIN sbm_parameters fx_opt ON fx_opt.param_name = 'fx_rw_sqrt2_option'
    JOIN sbm_parameters fx_rw  ON fx_rw.param_name  = 'fx_rw'
    JOIN sbm_parameters inv    ON inv.param_name    = 'inv_sqrt2'
    WHERE n.basis = :basis
)
SELECT basis, scope, risk_class, bucket, risk_factor, tenor_years, net_sensitivity,
       risk_weight, net_sensitivity * risk_weight
FROM rw;
