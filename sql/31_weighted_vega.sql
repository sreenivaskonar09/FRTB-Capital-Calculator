-- GIRR vega, step 3 (MAR21.4(3), MAR21.92): WS_k = RW * s_k with RW = min(55% * sqrt(60)/sqrt(10), 100%) = 100%.
-- The risk weight is a derived row in sbm_parameters ('vega_rw_girr') built from the cited inputs.
INSERT INTO res_weighted_vega
SELECT n.basis, n.scope, n.bucket, n.risk_factor, n.option_tenor, n.underlying_tenor, n.net_sensitivity,
       p.value, n.net_sensitivity * p.value
FROM res_net_vega n
JOIN sbm_parameters p ON p.param_name = 'vega_rw_girr'
WHERE n.basis = :basis;
