-- Steps 5-6 (MAR21.7): per scope and scenario, SUM delta + vega + curvature over risk classes; the binding
-- scenario is the one with the largest total; each risk class is then reported at that scenario.
-- Curvature enters at cmode = 'OPT' (instruments with optionality, MAR21.2).
INSERT INTO res_sbm_final
WITH comp AS (
    SELECT basis, scope, scenario, risk_class, delta_charge AS charge FROM res_delta_scenario WHERE basis = :basis
    UNION ALL
    SELECT basis, scope, scenario, risk_class, vega_charge FROM res_vega_scenario WHERE basis = :basis
    UNION ALL
    SELECT basis, scope, scenario, risk_class, curvature_charge FROM res_curvature_scenario
     WHERE basis = :basis AND cmode = 'OPT'
), cls AS (
    SELECT basis, scope, scenario, risk_class, SUM(charge) AS charge FROM comp GROUP BY basis, scope, scenario, risk_class
), tot AS (
    SELECT basis, scope, scenario, SUM(charge) AS total FROM cls GROUP BY basis, scope, scenario
), bind AS (
    SELECT basis, scope, scenario FROM (
        SELECT t.*, ROW_NUMBER() OVER (PARTITION BY basis, scope ORDER BY total DESC) AS rn FROM tot t)
    WHERE rn = 1
)
SELECT c.basis, c.scope, c.risk_class, b.scenario, c.charge
FROM cls c JOIN bind b ON b.basis = c.basis AND b.scope = c.scope AND b.scenario = c.scenario
UNION ALL
SELECT t.basis, t.scope, 'TOTAL', b.scenario, t.total
FROM tot t JOIN bind b ON b.basis = t.basis AND b.scope = t.scope AND b.scenario = t.scenario;
