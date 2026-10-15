-- Rules-based trading book / banking book flag, applied at BOOK level.
-- Order of rules follows BCBS d457 RBC25 (the boundary is RBC25, not MAR12):
--   R1 RBC25.3  legal impediment to sell/hedge          -> banking book
--   R2 RBC25.5  held for short-term resale, short-term price profit, arbitrage, or hedging
--               of those                                -> trading book
--   R3 RBC25.9  presumption: accounting trading assets / market-making -> trading book
--   R4 RBC25.7  anything else                           -> banking book
-- review_note: a heuristic consistency check. The 365-day threshold is NOT from the BCBS text.
UPDATE books SET
    trading_book_flag = CASE
        WHEN legal_impediment = 1 THEN 0
        WHEN stated_purpose IN ('short_term_resale','arbitrage','hedge_of_trading','market_making') THEN 1
        WHEN accounting_class = 'FVTPL_TRADING' THEN 1
        ELSE 0 END,
    classification_rule = CASE
        WHEN legal_impediment = 1 THEN 'RBC25.3 legal impediment'
        WHEN stated_purpose IN ('short_term_resale','arbitrage','hedge_of_trading') THEN 'RBC25.5 trading purpose'
        WHEN stated_purpose = 'market_making' THEN 'RBC25.5/25.9(2) market-making'
        WHEN accounting_class = 'FVTPL_TRADING' THEN 'RBC25.9(1) accounting trading asset'
        ELSE 'RBC25.7 no trading purpose' END,
    review_note = CASE
        WHEN stated_purpose IN ('short_term_resale','arbitrage','hedge_of_trading','market_making')
             AND expected_holding_days > 365 THEN 'REVIEW: trading intent but expected holding > 365d (heuristic)'
        WHEN stated_purpose IN ('hold_to_maturity','alm_accrual') AND accounting_class = 'FVTPL_TRADING'
             THEN 'REVIEW: banking purpose but fair-valued through P&L'
        ELSE NULL END;
