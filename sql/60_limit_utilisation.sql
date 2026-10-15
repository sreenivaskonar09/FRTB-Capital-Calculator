-- Limit monitoring: desk x risk class standalone SBM delta charge (binding scenario) vs synthetic limit.
-- A desk with no exposure in a risk class shows a zero charge (0% utilisation).
INSERT INTO res_limit_util
SELECT :basis, d.desk_name, l.risk_class, l.limit_amount,
       COALESCE(f.charge_at_binding, 0.0),
       COALESCE(f.charge_at_binding, 0.0) / l.limit_amount,
       CASE WHEN COALESCE(f.charge_at_binding, 0.0) > l.limit_amount THEN 1 ELSE 0 END
FROM limits l
JOIN desks d ON d.desk_id = l.desk_id
LEFT JOIN res_sbm_final f
       ON f.scope = d.desk_name AND f.risk_class = l.risk_class AND f.basis = :basis;
