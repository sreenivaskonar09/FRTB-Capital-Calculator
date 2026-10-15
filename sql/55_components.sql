-- Delta / vega / curvature split of each scope's charge at its binding scenario.
INSERT INTO res_sbm_components
WITH comp AS (
    SELECT basis, scope, scenario, risk_class, 'delta' AS component, delta_charge AS charge
      FROM res_delta_scenario WHERE basis = :basis
    UNION ALL
    SELECT basis, scope, scenario, risk_class, 'vega', vega_charge
      FROM res_vega_scenario WHERE basis = :basis
    UNION ALL
    SELECT basis, scope, scenario, risk_class, 'curvature', curvature_charge
      FROM res_curvature_scenario WHERE basis = :basis AND cmode = 'OPT'
), bind AS (
    SELECT DISTINCT basis, scope, binding_scenario AS scenario FROM res_sbm_final
     WHERE basis = :basis AND risk_class = 'TOTAL'
)
SELECT c.basis, c.scope, c.risk_class, c.component, c.scenario, c.charge
FROM comp c JOIN bind b ON b.basis = c.basis AND b.scope = c.scope AND b.scenario = c.scenario
UNION ALL
SELECT c.basis, c.scope, 'TOTAL', c.component, c.scenario, SUM(c.charge)
FROM comp c JOIN bind b ON b.basis = c.basis AND b.scope = c.scope AND b.scenario = c.scenario
GROUP BY c.basis, c.scope, c.component, c.scenario;
