# Falsification Criteria Template

Every thesis ships with falsification. No escape hatches, no moving goalposts.

## The four rules
1. **Time-bound** — "if X by date Y." No open-ended "if eventually."
2. **Numeric** — "DD exceeds 10R," not "strategy struggles."
3. **Pre-committed** — written before the trade, archived, immutable.
4. **Independent** — a third party could check whether the criteria triggered.

## Template

```
STRATEGY: _______

I will abandon this strategy if ANY of the following occur:

1. NUMERIC FAILURE
   Example: Live expectancy below +0.15R across first 50 trades
   _________________________________________

2. REGIME FAILURE
   Example: Net negative P&L across any 20-trade window in VIX<15
   _________________________________________

3. EXECUTION FAILURE
   Example: Avg slippage exceeds 2x backtest across any 10 trades
   _________________________________________

4. TIME-BOUND FAILURE
   Example: Strategy not profitable vs. costs by day 90
   _________________________________________

5. CATASTROPHIC FAILURE
   Example: Any single loss exceeds 3x intended max risk
   _________________________________________

These criteria are immutable for this strategy version.
Changing them requires shipping a new version with a new memo.

Signed: _______   Date: _______
```

## Why this matters

Without falsification criteria, you are the falsification criterion. And you are not a reliable judge when your money is on the line. The most dangerous moment for a strategy is down 8R, deciding whether it's "in a normal drawdown" or "broken." Without pre-committed criteria, you will almost always decide "normal drawdown" and you will almost always be wrong.

The template forces the decision when you're sober. Trust the sober version.
