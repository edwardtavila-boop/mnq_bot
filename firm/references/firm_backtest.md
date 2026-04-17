# Backtesting the Firm Itself (Tier 4)

The most neglected discipline in multi-agent systems: does running the Firm actually produce better decisions than the raw strategy?

## The setup

Two parallel runs on identical historical data.

**Baseline:** raw strategy, no agent oversight. Takes every signal. Pre-set sizing.
**Firm-Augmented:** same signals, routed through agent workflow. Agents can veto, modify size, recommend hold.

## What to measure

| Metric | Definition |
|--------|-----------|
| Raw return delta | Firm − Baseline |
| Sharpe delta | Risk-adjusted return improvement |
| Sortino delta | Downside-adjusted improvement |
| Max DD delta | Drawdown reduction |
| Trade count delta | How many trades did the Firm kill? |
| Latency cost | Decision time × slippage per ms |

## Veto analysis

Of trades the Firm blocked that Baseline took:
- What was the average outcome?
- What % were net losers?
- Avg R?

If blocked trades averaged **+0.5R**, the Firm's risk layer is too tight — killing winners.
If blocked trades averaged **−1.2R**, the Firm is catching real danger.

## Override analysis (live only)

Every override outcome tracked. Override hit rate vs. Firm-approved hit rate.

If your overrides consistently underperform the Firm, you are not smarter than your process. Stop overriding.

## Complexity budget (pre-committed)

> "The Firm must add ≥0.3 Sharpe OR reduce max DD by ≥25% vs. baseline, or I simplify by removing agents until it does, or I dissolve the Firm and trade raw."

Without this commitment, agent systems grow forever because adding agents feels productive. Most Firms should have fewer agents than they do.

## Walk-forward requirement

You cannot truly backtest agents with hindsight. Two defenses:

1. **Walk-forward replay** — feed agents decisions one at a time, only info available at that timestamp. Onerous. That's the point.
2. **Held-out period** — design agent charters without reference to specific periods, then test on data none of the designs were tuned on.

The charters themselves can be overfit. "Red Team should watch for regime fragility" is learned from specific blowups. If those blowups are in the test set, the Firm has a hidden advantage.

## Ablation testing — quarterly

Turn off one agent at a time, re-run backtest.

| Config | Return | Sharpe | DD |
|--------|--------|--------|-----|
| Full Firm | | | |
| Firm − Quant | | | |
| Firm − Red Team | | | |
| Firm − Risk | | | |
| Firm − Macro | | | |
| Firm − Micro | | | |

If removing an agent doesn't hurt performance, **that agent is overhead.** Remove them.

You will find your favorite agent contributes nothing. You will find the agent you almost cut is carrying everything. The ablation doesn't care what you prefer.

## The verdict matrix

| Result | Action |
|--------|--------|
| Firm wins on return AND Sharpe AND DD | Keep, expand |
| Firm loses return but wins Sharpe + DD | Loosen vetoes, keep risk agents |
| Firm wins return but worse DD | Suspicious — more data, re-test |
| Firm ~= Baseline | Firm is overhead — simplify or dissolve |
| Firm loses on everything | Dissolve, trade raw strategy |

Most retail Firms land in the bottom two rows and refuse to admit it. **Commit in writing to act on the verdict before running the test.** That commitment is the only thing that makes the test meaningful.
