# Handoff: continue building mnq_bot

You are picking up an in-progress trading-system codebase. Read this
file completely before doing anything else, then read README.md, then
start.

## Your scope

Continue building this system from the skeleton. The skeleton has the
foundational files implemented (`[REAL]`) and the rest specified as
`[CONTRACT]` modules with precise docstrings telling you exactly what
each must do. Your job: turn `[CONTRACT]` modules into `[REAL]` ones,
in the order below.

You are not authorized to:
- Modify the spec schema (`src/mnq/spec/schema.py`) without a written
  proposal in `docs/proposals/` and explicit human approval.
- Modify the v0.1 baseline spec (`specs/strategies/v0_1_baseline.yaml`).
- Loosen any gauntlet gate threshold, ever.
- Add code paths that bypass the kill switch or the risk manager.
- Promote anything to live (the `tier: live` field in any spec).

You are encouraged to:
- Add unit tests as you implement each module (write to
  `tests/level_1_unit/test_<module>.py`).
- Run `make test-1` after each module and ensure it stays green.
- Add notes to `docs/IMPLEMENTATION_NOTES.md` when a contract was
  ambiguous and you made a choice — flag for human review.

## Implementation order

Do these in sequence. Do not skip ahead. Each step has a definition
of done.

### Step 1 — Tradovate venue client

Files: `src/mnq/venues/tradovate/{auth,rest,ws}.py`

Reference `docs/TRADOVATE_NOTES.md` (you may need to create this from
the official docs at https://api.tradovate.com — fetch and summarize
the auth flow, WS frame protocol, and the OCO bracket endpoint).

Key facts to bake in:
- Tokens expire at 90 minutes; renew via `/auth/renewAccessToken` at
  ~75 minutes elapsed. Do NOT call `/auth/accessTokenRequest` to
  refresh — that creates a new session and you only get 2 concurrent.
- WS frame format: text frames with single-char prefixes — `o` (open),
  `h` (heartbeat), `a` (array of JSON), `c` (close).
- WS heartbeat is `[]` empty array every 2.5s. Treat >7s without
  heartbeat as disconnected regardless of socket state.
- Two separate WS endpoints: market data vs. orders/account.
- Demo subdomain: `demo.tradovateapi.com`. Live: `live.tradovateapi.com`.
- Use OCO via `orderstrategy/startorderstrategy` for atomic bracket
  placement. Never have a position without protection — if bracket
  placement fails after entry fill, immediately market-close.

Definition of done:
- `mnq venue tradovate auth-test` succeeds against paper.
- `mnq venue tradovate list-accounts` returns the user's accounts.
- A round-trip integration test (`tests/level_5_integration/test_tradovate_smoke.py`)
  places a 1-contract market order on paper, sees the fill via WS, and
  closes the position. SKIPPED if no credentials in env.
- All level-1 tests still green.

### Step 2 — Pine v6 generator

Files: `src/mnq/generators/pine/__init__.py`, `src/mnq/generators/pine/generator.py`

Generates Pine v6 source from a `StrategySpec`. Walks the AST of every
condition; emits Pine for each node type via a Visitor subclass.

Reference: see the Pine v6 example in the chat history that produced
this skeleton (the v0.4.2 example). The generator emits a structurally
identical file but parameterized by spec contents.

Strict rules:
- Always emit `//@version=6` as the first line.
- Always emit `use_bar_magnifier = true` and
  `process_orders_on_close = false` in `strategy(...)`.
- HTF features always use `request.security` with
  `lookahead = barmerge.lookahead_off`. NEVER `lookahead_on`.
- All alert messages are JSON literals matching the schema in
  `docs/ALERT_CONTRACT.md` (you'll need to write that doc — pull
  the schema from the chat history's Part 4).
- Do not emit `strategy.risk.*` functions; risk lives in the executor.
- Run static checks before writing the file:
  no `lookahead_on`, no raw `security(`, no nested `if` with side effects.

Definition of done:
- `mnq spec render specs/strategies/v0_1_baseline.yaml --target pine`
  produces a `.pine` file in `specs/generated_pine/`.
- The output passes a Pine syntax check (you can use a regex-based
  check; a real syntax check requires TradingView).
- Snapshot test: rendering v0_1_baseline.yaml twice produces
  byte-identical output.

### Step 3 — Python executor generator

Files: `src/mnq/generators/python_exec/{__init__,generator,base}.py`

Generates a `StrategyBase` subclass per spec. The base class lives in
`generators/python_exec/base.py` (you write this) and provides the
common machinery (`on_bar`, feature update loop, risk manager
integration). The generated subclass implements `_eval_long`,
`_eval_short`, and `_compute_stop_distance` from the spec.

Definition of done:
- `mnq spec render specs/strategies/v0_1_baseline.yaml --target python`
  produces a `.py` file in `specs/generated_python/`.
- The generated file imports cleanly and instantiates without error.
- A Layer 2 sim run on a synthetic 100-bar dataset produces some
  deterministic signals.

### Step 4 — Feature library with parity tests

Files: `src/mnq/features/{ema,sma,rma,atr,vwap,rvol,htf}.py`

Each feature: float64 internal, tick-quantized at boundaries, with a
unit test asserting equality to a reference Pine output stored in
`tests/fixtures/pine_reference/`.

For now you do NOT have real Pine reference data; create the fixtures
as synthetic but mathematically known cases (e.g., constant input ->
EMA = constant). Replace with real Pine outputs once you can scrape
TradingView.

Definition of done:
- All feature unit tests in `tests/level_1_unit/test_features_*.py`
  pass.
- The features cleanly drop into the Python executor generator.

### Step 5 — Layer 2 sim (event-driven)

Files: `src/mnq/sim/layer2/{__init__,engine,fills,latency}.py`

Implements the bar-driven event loop with the conservative OHLCV
intrabar reconstruction described in `docs/ARCHITECTURE.md` (Part 2
of the original design).

Definition of done:
- Layer 2 runs the v0.1 spec on a synthetic 1-day dataset and
  produces a trade ledger.
- Determinism test: two runs with same seed produce identical output.

### Step 6 — MCP server with the read-only tool subset

Files: `src/mnq/mcp/{server,tools/__init__,tools/read_only.py}`

Implement these tools:
- `get_strategy(version)`
- `list_strategy_versions()`
- `get_executor_state()`
- `get_session_pnl(spec_hash?)`
- `get_recent_fills(since, spec_hash?)`
- `get_risk_utilization()`
- `get_ws_health()`
- `get_open_orders(venue)`

Defer write tools (pause/flatten/etc.) until a later step.

Definition of done:
- `mnq mcp serve --transport stdio` starts and registers all tools.
- A test client can call each tool and get a structured response
  (or a clear "not yet wired" error if the underlying state isn't
  populated).

### Step 7 — Calibration harness

Files: `src/mnq/calibration/fit_slippage.py`

Per-regime OLS fit of `slippage_ticks = a + b * bar_atr_ticks` from
shadow fills.

Definition of done:
- Synthetic-input test: feed in 200 fake fills with known a and b,
  recover within 5%.

### Step 8 — Attribution metrics (the new gates 13/14)

Files (already CONTRACT-stubbed):
- `src/mnq/gauntlet/benchmarks.py`
- `src/mnq/gauntlet/metrics_attribution.py`
- `src/mnq/gauntlet/gates/gate_attribution.py`

Implement per the contracts. Use Newey-West HAC standard errors for
alpha; reference `statsmodels.regression.linear_model.OLS` with the
`get_robustcov_results(cov_type='HAC', cov_kwds={'maxlags': L})`
helper to keep the math right.

Definition of done:
- All property tests in `tests/level_2_property/test_attribution.py`
  pass (see contract docstring for required tests).
- Synthetic strategies behave as predicted (pure-beta fails alpha,
  constant-edge passes both, naive-momentum-clone fails on the
  naive_momentum benchmark specifically).

## Process notes

- Commit after each step. Use Conventional Commits.
- If a contract is genuinely ambiguous, write your interpretation in
  `docs/IMPLEMENTATION_NOTES.md` and proceed; flag for human review.
- If a test you write feels redundant with what the contract says,
  it probably is — that's the right amount of verification.
- The 8-level test pyramid in `Makefile` is the law. Don't run level N
  until level N-1 is green.

## Things you might be tempted to do that you should NOT

- "Optimize" the v0.1 baseline spec. Don't. It's the baseline by
  design. The agent's job is to find better; yours is to build the
  scaffolding.
- Add new features to the spec schema "while you're in there." Don't.
  Schema changes go through the human-approval path.
- Make the gauntlet faster by parallelizing aggressively. The goal of
  the gauntlet is correctness, not speed; correctness-preserving
  parallelism (e.g., per-CPCV-path workers) is fine, but don't trade
  any determinism for speed.
- Implement the agent (`src/mnq/agents/`). That's a separate phase
  after all the infrastructure is real. Implementing the agent against
  contracts that aren't real yet means it'll be optimizing against
  stubs, which is worse than not having it.

Good luck.
