# EVOLUTIONARY TRADING ALGO // Equity Sniper

Self-learning MNQ/NQ scalping system. 8-axis confluence engine, 6-agent adversarial
review pipeline, adaptive learner, walk-forward optimized sizing, Tradovate execution,
TradingView Pine v6 visualization, 12-gate gauntlet, human-only live promotion.

> **Modes:** `PAPER SIM` (simulated fills, journal-backed) · `LIVE` (real venue, human-gated)

This README is the **map**. Read it first.

---

## Status legend

Throughout the codebase, every module top-docstring carries a status tag:

- `[REAL]` — implemented, tested, usable today.
- `[CONTRACT]` — specification only. The docstring tells you exactly what
  this module must do; the body raises `NotImplementedError`. Fill in
  deterministically.
- `[STUB]` — minimal placeholder, not yet specified in detail. Treat as
  TODO.

`grep -rn '\[CONTRACT\]\|\[STUB\]' src/` lists everything still to build.

---

## Architecture (one screen)

```
                        ┌──────────────────────┐
                        │  Specs (YAML in git) │  ← human-written + agent-proposed
                        └─────────┬────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
       generators/pine    generators/python_exec   sim layers 1/2/2.5
              │                   │                   │
              ▼                   ▼                   ▼
       *.pine on TV         StrategyBase class   gauntlet metrics
       (viz + parity)             │                   │
                                  ▼                   ▼
                            executor + venues    agents read results
                            (Tradovate REST/WS)   ↑ propose new specs
                                  │                  via GA
                                  ▼
                              positions, fills
                                  │
                                  ▼
                           calibration loop ──── back to sim
```

The MCP server exposes everything to chat / Claude Code / Cowork through one
tool surface (`src/mnq/mcp/`).

---

## Repo layout

```
src/mnq/
  core/          shared types: Bar, Tick, Side, Decimal helpers, time/tz
  spec/          YAML schema, AST, validator, hash, mutation operators
  features/      EMA, ATR, VWAP, RVOL, HTF — float internals, tick-quantized at boundary
  generators/
    pine/        spec → Pine v6 source
    python_exec/ spec → StrategyBase Python class
  sim/
    layer1/      vectorized OHLCV, conservative ties — RL inner loop
    layer2/      event-driven, intrabar reconstruction — gauntlet engine
    layer25/     Layer 2 driving real executor through mock WS — integration
  gauntlet/      the 12 gates: CPCV, DSR, PSR, regime, stress, perturb, parity
  agents/        7 specialist agents (one module each, prompts in agents/prompts/)
  executor/      order state machine, risk manager runtime, reconciliation
  venues/
    tradovate/   REST + WS clients, auth, order/strategy primitives
    mock/        in-process mock WS for tests + Layer 2.5
  calibration/   shadow-fill → slippage/latency model fitter (per regime)
  mcp/           MCP server + tool implementations
  cli/           operational CLI (kill, pause, status, deploy)
  storage/       DuckDB schema, parquet I/O, append-only logs
  observability/ structured logging, metrics, dashboard exporters

specs/
  strategies/    canonical YAMLs, one file per version, semver-named
  proposals/     pending agent proposals (branches in git, also indexed here)
  generated_pine/    *.pine emitted by the Pine generator (hash-named)
  generated_python/  *.py emitted by the Python executor generator
  experimental/  hand-written exotic specs — agent reads results, never mutates

data/
  bars/          parquet, partitioned symbol=MNQ/year=YYYY/month=MM
  ticks/         parquet, same partitioning (when available)
  fills/         shadow + live fills for calibration
  calendars/     CME holidays, economic events

tests/level_N_*/  the 8-level pyramid (see TESTING.md)
```

---

## Quickstart (today, with the skeleton)

```bash
# 1. Install
cp .env.example .env
# edit .env with your Tradovate paper credentials
uv sync

# 2. Run level-1 tests (pure, no broker, no data)
uv run pytest tests/level_1_unit -v

# 3. Render the v0.1 spec to Pine v6
uv run mnq spec render specs/strategies/v0_1_baseline.yaml

# 4. Authenticate to Tradovate paper
uv run mnq venue tradovate auth-test

# 5. Start the MCP server (so Chat/Code/Cowork can call tools)
uv run mnq mcp serve --transport stdio
```

Until level-1 is green, level-2 won't run. Until level-5 is green, you cannot
run paper soak. The `Makefile` enforces this.

---

## What's actually built right now

`[REAL]`:

- `core/` (decimal helpers, types, time)
- `spec/` (schema, validator, hash) — partial; AST and mutation are CONTRACT
- `venues/tradovate/auth.py` (full auth flow with renewal)
- `venues/tradovate/rest.py` (REST endpoints we use)
- `venues/tradovate/ws.py` (text-frame protocol, heartbeat tracking, reconnect)
- `mcp/server.py` + a handful of tools (status, kill, pause, get_strategy)
- `calibration/fit_slippage.py` (per-regime OLS fit)
- `specs/strategies/v0_1_baseline.yaml` + matching `.pine`

`[CONTRACT]` — every other file. Each contains a precise spec for what it
must do.

---

## Documents

- `docs/ARCHITECTURE.md` — full design (see also: the chat history that
  produced this).
- `docs/TESTING.md` — the 8-level pyramid, gating mechanism.
- `docs/AGENTS.md` — the 7 specialist agents, prompts, allowed tools.
- `docs/RUNBOOK.md` — what to do when things go wrong.
- `docs/TRADOVATE_NOTES.md` — gotchas accumulated from real integration.

---

## Hard rules (do not break)

1. The Pine generator never emits `lookahead_on`. Ever.
2. The agent never sees the most recent 60 days of MNQ data.
3. Live promotion is human-only. No exception, no override.
4. The 12 gauntlet gates are all required. Do not loosen them when nothing
   is passing — passing nothing is the gauntlet working correctly.
5. Risk caps cannot be modified at runtime — only via spec change + redeploy.
6. The kill switch is callable from anywhere and never refuses.
