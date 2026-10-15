-- Report queries used by the Excel builder (read-only). Each block starts with "-- name: <key>".
-- name: by_desk_class
SELECT f.scope AS desk, f.risk_class, f.binding_scenario, f.charge_at_binding AS charge
FROM res_sbm_final f WHERE f.basis = :basis AND f.scope <> 'FIRM' ORDER BY f.scope, f.risk_class;
-- name: scenarios
SELECT s.scope, s.risk_class, s.scenario, s.delta_charge
FROM res_delta_scenario s WHERE s.basis = :basis ORDER BY s.scope, s.risk_class, s.scenario;
-- name: diversification
SELECT 'sum of desks (standalone)' AS item, SUM(charge_at_binding) AS value FROM res_sbm_final
 WHERE basis = :basis AND scope <> 'FIRM' AND risk_class = 'TOTAL'
UNION ALL
SELECT 'firm (single book)', charge_at_binding FROM res_sbm_final
 WHERE basis = :basis AND scope = 'FIRM' AND risk_class = 'TOTAL';
-- name: book_flags
SELECT d.desk_name, b.book_name, b.stated_purpose, b.accounting_class, b.expected_holding_days,
       b.trading_book_flag, b.classification_rule, b.review_note,
       COUNT(p.position_id) AS n_positions
FROM books b JOIN desks d ON d.desk_id = b.desk_id LEFT JOIN positions p ON p.book_id = b.book_id
GROUP BY b.book_id ORDER BY d.desk_name, b.book_name;
-- name: top_weighted
SELECT risk_factor, net_sensitivity, risk_weight, weighted_sensitivity
FROM res_weighted_sens WHERE basis = :basis AND scope = 'FIRM'
ORDER BY ABS(weighted_sensitivity) DESC LIMIT 15;
