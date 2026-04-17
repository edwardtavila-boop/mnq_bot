# Implementation notes — flagged for human review

Running log of judgment calls made while turning `[CONTRACT]` modules into
`[REAL]` ones. Each step is its own section. Items tagged **🚩 REVIEW** are
the ones worth a human second opinion before the next layer builds on them.

---

## Step 1 — Tradovate venue client (2026-04-14)

### Delta between the skeleton's README and the actual tree

The README (§ *What's actually built right now*) lists several modules as
`[REAL]` that do **not** exist in the checkout — specifically
`venues/tradovate/{auth,rest,ws}.py`, `mcp/server.py`, several spec-module
helpers, and `calibration/fit_slippage.py`. Those files were absent from
the skeleton tarball.

I treated `docs/HANDOFF_TO_COWORK.md` as ground truth (it says to turn
`[CONTRACT]` into `[REAL]` in order), and implemented the Tradovate venue
client from scratch.

**🚩 REVIEW:** is the README's status list stale, or was the tarball
pruned before bundling? Either way, the README's §*What's actually built*
should be rewritten to reflect reality after each step completes. I did
not edit it — it belongs to you.

### Files added this step

```
docs/TRADOVATE_NOTES.md                     new — distilled API reference
docs/IMPLEMENTATION_NOTES.md                new — this file

src/mnq/venues/__init__.py                  new
src/mnq/venues/tradovate/__init__.py        new — public surface exports
src/mnq/venues/tradovate/config.py          new — Environment + Hosts
src/mnq/venues/tradovate/auth.py            new — login, renew, Token
src/mnq/venues/tradovate/rest.py            new — REST client + BracketParams
src/mnq/venues/tradovate/ws.py              new — frame parser + async client

src/mnq/cli/__init__.py                     new
src/mnq/cli/main.py                         new — typer root app
src/mnq/cli/venue.py                        new — `mnq venue tradovate ...`

tests/__init__.py                           new
tests/level_1_unit/__init__.py              new
tests/level_1_unit/conftest.py              new — auto-marks `level1`
tests/level_1_unit/test_venue_tradovate_auth.py     new — 23 tests
tests/level_1_unit/test_venue_tradovate_config.py   new — 9 tests
tests/level_1_unit/test_venue_tradovate_rest.py     new — 16 tests
tests/level_1_unit/test_venue_tradovate_ws.py       new — 18 tests
tests/level_5_integration/__init__.py       new
tests/level_5_integration/conftest.py       new — auto-marks `level5`
tests/level_5_integration/test_tradovate_smoke.py   new — skipped w/o creds
```

### Definition-of-done status

- `mnq venue tradovate auth-test` — **wired**. Runs against the injected
  httpx client and prints a rich table of token claims. Not yet run
  against paper (needs `.env`).
- `mnq venue tradovate list-accounts` — **wired**. Same.
- Round-trip integration test at `tests/level_5_integration/test_tradovate_smoke.py`
  — **written, skipped** when any of the required env vars is missing.
  Currently skipped.
- Level-1 tests — **66 passed** (`uv run pytest tests/level_1_unit -m level1 -v`).

### Interpretations I made (and should be sanity-checked)

1. **🚩 REVIEW — market-data WebSocket host split.** I used
   `wss://md-demo.tradovateapi.com/v1/websocket` for demo and
   `wss://md.tradovateapi.com/v1/websocket` for live. This is the JS SDK
   convention; official docs don't explicitly confirm the `md-demo` prefix
   for paper. If the first real connect during Step 1's smoke test 404s,
   switch demo's `market_data_ws` in `config.py` — everything above it is
   parameterized.

2. **🚩 REVIEW — `orderStrategyTypeId = 2`**. Hard-coded in
   `BracketParams.to_request_body`. Forum + Tradovate staff confirmed "2 is
   the built-in Brackets strategy (currently the only one)." Recommended
   defense: on session start, call `GET /orderStrategyType/list` and log a
   warning if `type.name == "Brackets"` isn't at `id == 2`. I did not add
   that probe yet — could go into the executor's startup.

3. **🚩 REVIEW — signed tick semantics.** `BracketParams.profit_target_ticks
   > 0` and `stop_loss_ticks < 0` is validated at the dataclass level. I
   inferred this holds for both Buy and Sell entries (forum example is Buy
   only). If the smoke test's "place a 1-contract Sell" bracket returns an
   error, the sign convention flips for Sell and I'll need to negate on
   encode.

4. **Heartbeat cutoffs.** Client sends `[]` every 2.5s; server inbound
   gap > 7s triggers force-disconnect. Derived from HANDOFF §3 + the
   community forum's "2–2.5s heartbeat" guidance. Official docs are vague.
   These are hard-coded constants in `ws.py`; feel free to loosen to 10s
   if the smoke test sees false positives.

5. **Error classification in auth.** `_classify_error_text` pattern-matches
   on keywords ("session"+"limit"/"concurrent"/"maximum" → SessionLimitError,
   "password"/"credential"/"locked"/"captcha"/"invalid" → InvalidCredentialsError,
   else AuthError). Tradovate does not return structured error codes, so
   this will need tuning as real error strings surface from paper.

6. **REST 200-with-`errorText` / `failureReason` handling.** Defensive:
   any 2xx response carrying either field raises `OrderRejectedError`. This
   is conservative — if some legitimate Tradovate response includes
   `errorText: ""` or similar, the truthy check will skip it. Empty strings
   are falsy so that case is fine; if Tradovate ever ships a success
   payload with a non-empty informational `errorText`, we'll find out.

7. **`BracketParams.to_params_json`.** The `params` field is a JSON *string*,
   not a nested object. This is a common foot-gun in third-party Tradovate
   clients. The unit test `test_params_is_stringified` locks this in.

8. **Renew timing — 75 min vs docs' 85 min.** HANDOFF says renew at ~75
   min elapsed; partner docs recommend ~85 min. I went with 75 (handoff
   spec, more conservative, 15 min safety margin against clock skew).
   Threshold is `DEFAULT_RENEW_AT` — trivial to change.

9. **Absolute-max-age token guard.** I added `ABSOLUTE_MAX_AGE = 88 min`
   in `auth.py`: even if the server clock says "not expired", we treat the
   token as dead past 88 min since local issue. Defensive — protects
   against clock drift where the server says "you're good until 14:00 UTC"
   but it's actually 14:05 UTC server-side. Not in the handoff spec.

10. **No background renewer yet.** `TradovateRestClient` takes a
    `token_provider: Callable[[], Token]`. The renewer that swaps what
    that callable returns is a concern for the executor layer (Step 5-ish).
    Step 1's CLI invocations do a single `login()` and bounce out, so no
    renewer is needed for DoD.

11. **Python version drift.** The dev venv was built with Python 3.14 by
    default because `uv`'s `requires-python = ">=3.12"` picks the newest
    interpreter installed. Ran tests on 3.12 explicitly to match the
    `target-version` pin in `[tool.ruff]`. Both pass. Consider pinning
    `.python-version` to `3.12` to lock this.

12. **Dev-env location.** The `.venv` could not be recreated inside the
    mounted workspace because the host filesystem (FUSE) refuses to remove
    hidden files `uv` leaves behind. I ran tests via
    `UV_PROJECT_ENVIRONMENT=/tmp/mnq-venv UV_LINK_MODE=copy uv run …`.
    Recommend adding `UV_PROJECT_ENVIRONMENT` and `UV_LINK_MODE=copy` to
    the Makefile or a `scripts/activate-venv.sh` so next-session Cowork
    doesn't re-hit this.

### Known residual warts

- The `__init__.py` at `src/mnq/venues/tradovate/` re-exports the full
  surface. If/when the public API stabilizes, audit what should and
  shouldn't be re-exported (e.g. internal helpers like `parse_frame` are
  useful for tests but arguably shouldn't be in the stable surface).
- `cli/venue.py` does a tiny inline `.env` loader as a fallback when
  `python-dotenv` isn't present. `python-dotenv` *is* in the `[project]`
  dependencies, so the fallback path is dead in production — worth
  removing for clarity if reliability is ok.
- `TradovateWsClient.run()` raises on `HARD_DISCONNECT_AFTER` consecutive
  failures. The executor needs to catch that and flatten. I did not add
  that wiring; it's an executor-layer concern.

---

## Step 2 — Pine v6 generator (2026-04-14)

### Files added

```
docs/ALERT_CONTRACT.md                           new — JSON alert schema
src/mnq/generators/__init__.py                   new
src/mnq/generators/pine/__init__.py              new — public surface
src/mnq/generators/pine/generator.py             new — renderer + static check
src/mnq/cli/spec.py                              new — `mnq spec render|rehash`
tests/level_1_unit/test_pine_generator.py        new — 33 tests
specs/generated_pine/mnq_baseline_v0_1.pine      new — snapshot output
```

### DoD status

- `mnq spec render specs/strategies/v0_1_baseline.yaml --target pine` — **wired**,
  writes `specs/generated_pine/mnq_baseline_v0_1.pine` (4738 bytes).
- Snapshot test `test_byte_identical_snapshot` — **passes** (two renders equal).
- Static check rejects `lookahead_on`, raw `security(`, and `strategy.risk.*`
  — covered by unit tests.
- Level-1 green: 99 passed (was 66; +33 pine tests).

### Interpretations I made (and should be sanity-checked)

1. **🚩 REVIEW — alert JSON assembly.** Pine v6 has no `str.format_json`.
   The generator builds the JSON payload via explicit string concatenation
   and a `_json_str` helper. String-typed fields (`spec_id`, `spec_hash`,
   `symbol`) are wrapped in `"`-escaped double quotes; numeric fields are
   `str.tostring(...)`. If the spec ever has an id or hash containing a
   `"` or `\\` (shouldn't — both are hex-only or `[A-Za-z0-9_]`) the
   alert body will be corrupt. The spec schema should gain a regex
   validator for `strategy.id` to lock this in; not worth changing the
   schema today.

2. **🚩 REVIEW — `mirror_of: long`.** For the baseline spec, `short` is
   `mirror_of: long`, so the generator textually mirrors each long
   condition (>/<, crosses_above/below, rising/falling). This is a
   string-level flip, so any condition whose "mirror" isn't a simple
   sign swap (e.g., conditions without a natural dual) will be rendered
   as-is. For v0.1 baseline this is safe — every condition has a clear
   mirror. If a future spec uses `mirror_of` with asymmetric features,
   flag and tighten the mirror logic.

3. **🚩 REVIEW — session window encoding.** I used the Pine idiom
   `time(timeframe.period, "HHMM-HHMM", "<tz>")` for enabled windows and
   hard-false for disabled. Disabled windows *are* emitted as
   `false`-named booleans so the ast's `session_window in [...]`
   reference resolves to the right identifier. No alternative tested.

4. **🚩 REVIEW — blackouts are executor-authoritative.** Pine
   approximates `session_offset` blackouts via `_bars_since_session_open`
   counting (assumes primary tf ≤ 1m → seconds/60). Economic-event
   blackouts (FOMC, etc.) render as `false` with a comment; they are
   enforced by the executor. The executor-layer step will need to wire
   an event-calendar source and NOT rely on Pine for this.

5. **🚩 REVIEW — `use_bar_magnifier = true` on a strategy whose primary
   timeframe is 1m.** Bar magnifier affects intrabar fills. On a 1m
   baseline this barely does anything (magnifier's minimum granularity
   is 1s), but the handoff mandates it. Cost: TradingView charges a
   small premium. Left on per spec.

6. **🚩 REVIEW — HTF `request.security` continues to use the spec's
   context timeframe.** If someone sets `features[i].timeframe = "4h"`
   with a primary of `1m`, Pine will hit request.security budget limits.
   The spec validator should reject that combination; this generator
   currently trusts the input.

7. **Static check false-positive guard.** Before regex-matching forbidden
   tokens (`lookahead_on`, raw `security(`, `strategy.risk.`), the
   checker strips Pine line comments so our own `// lookahead_on is
   forbidden`-style comments don't trip the scan. Same principle used
   for the header banner.

8. **Alert body uses concat rather than Pine's `str.format`.** `str.format`
   with many placeholders is awkward and less inspectable; concat is
   trivially correct and greppable. Doc updated to reflect this.

### Known residual warts

- The `_entry_json` function body spans 13 lines of concatenation. If a
  future alert adds a `context` sub-object (ALERT_CONTRACT §entry), the
  generator will need a helper per feature. Not wired today.
- No exit-event alert is emitted yet (ALERT_CONTRACT defines `exit`,
  `cancel`, `diagnostic` — only `entry` is implemented in Pine). The
  executor currently reconciles positions via WS fills, not Pine exit
  alerts, so shipping entry-only is safe for v0.1. File: IT.

---

## Step 3 & 4 — Python executor generator + Feature library (2026-04-14)

Combined since the generator's "imports cleanly" DoD requires the
feature library to exist. Both steps shipped together.

### Files added

```
src/mnq/features/__init__.py                          new
src/mnq/features/_source.py                           new
src/mnq/features/ema.py                               new
src/mnq/features/sma.py                               new
src/mnq/features/rma.py                               new
src/mnq/features/atr.py                               new
src/mnq/features/vwap.py                              new
src/mnq/features/rvol.py                              new
src/mnq/features/htf.py                               new
src/mnq/generators/python_exec/__init__.py            new
src/mnq/generators/python_exec/base.py                new — StrategyBase + HistoryRing
src/mnq/generators/python_exec/generator.py           new — render_python
tests/level_1_unit/_bars.py                           new — synthetic Bar helpers
tests/level_1_unit/test_features.py                   new — 16 tests
tests/level_1_unit/test_python_generator.py           new — 11 tests
specs/generated_python/mnq_baseline_v0_1.py           new — snapshot output
```

### DoD status

- `mnq spec render … --target python` — **wired**, writes
  `specs/generated_python/mnq_baseline_v0_1.py` (5280 bytes).
- Generated file imports cleanly + `build(spec)` returns a ready
  `GeneratedStrategy` — covered by unit test.
- Feed 100 synthetic bars through the generated strategy → no exception.
- Determinism: two runs with identical synthetic input produce identical
  signal lists — covered by `test_determinism_across_two_runs`.
- Feature reference tests: constant input → constant EMA/RMA/VWAP;
  monotone input → positive ATR; HTF no-lookahead lock — passing.
- Level-1 green: 126 passed (was 99; +16 feature tests, +11 generator
  tests).

### Interpretations & 🚩 REVIEW flags

1. **🚩 REVIEW — Pine-parity for features.** Reference tests use
   synthetic-known cases (constant input → constant output, etc.) per
   the handoff's instruction. Real Pine reference fixtures in
   `tests/fixtures/pine_reference/` are deferred until someone can
   scrape TradingView outputs. The current tests will not catch a
   Pine-vs-Python numerical disagreement in the *transient* phase of
   EMA/RMA seeding.

2. **🚩 REVIEW — Seed convention for EMA/RMA.** I used Pine's convention
   (seed with SMA of first N values, emit None until then). Some
   libraries emit partial EMA from the very first value. Locking this
   in matters for near-cross behavior in the first 10-ish bars of a
   session.

3. **🚩 REVIEW — VWAP day-boundary detection.** Session reset uses the
   UTC calendar date from the bar timestamp. This is wrong for
   futures whose session spans midnight UTC (CME futures session starts
   18:00 ET / 22:00 or 23:00 UTC). For the v0.1 baseline, which only
   trades the RTH window 09:30–15:55 ET, this is fine — every bar in a
   session has the same UTC date. For overnight-session strategies, the
   wrapper needs to accept a `day_key_fn` tied to the spec's session
   timezone + open time. I exposed that parameter on `VWAP.__init__`
   but the generator doesn't wire a custom key today.

4. **🚩 REVIEW — ATR uses `RMA._rma_step` directly.** The ATR class
   reaches into `RMA`'s private state to advance by a scalar TR (rather
   than a `Bar`). This couples the two classes; if RMA ever changes
   internals, ATR breaks. Could clean up by giving RMA a `feed_scalar`
   method.

5. **🚩 REVIEW — Stop/target clamping.** `StrategyBase._build_signal`
   clamps `stop_ticks` to `[min_ticks, max_ticks]` from the spec. TP
   is computed *after* the clamp (from clamped stop). The Pine file
   does the same — matching behavior. This means the effective R is
   adjusted when the clamp bites; spec authors should be aware.

6. **🚩 REVIEW — Bar history for `Builtin` operands.** The generator's
   `_resolve` helper returns None for past values of `close/open/etc.`
   because the base class doesn't ring-buffer raw Bar values. In
   practice the baseline spec only uses `close` on the current bar
   (`close > feature:vwap_session`), so this is fine. If a future spec
   uses `close on_bar 2 > open on_bar 2`, the generator will silently
   evaluate False. Easy to add a `_bar_history` ring in the base class
   if/when needed.

7. **🚩 REVIEW — Mirror_of textual flip.** Same caveats as the Pine
   generator. The short-side conditions are produced by regex-swapping
   `>`/`<`, `crosses_above`/`crosses_below`, `rising`/`falling`. Works
   for v0.1 baseline. More complex mirrorable conditions (e.g.
   `feature:cumulative_delta > N`) are NOT automatically mirrored —
   they'd need explicit `short:` definitions.

8. **🚩 REVIEW — HTF bucket alignment.** HTFWrapper buckets are
   UTC-epoch anchored. TradingView's HTF bars anchor to the session
   open, which for CME futures aligns naturally with UTC for the RTH
   window but not overnight. If/when overnight strategies ship, this
   needs a session-aware bucketer.

### Known residual warts

- The generated Python file doesn't currently emit inline comments from
  the spec's rationale or condition strings. Debugging a generated
  file requires cross-referencing against the source spec.
- The base class's `on_bar` doesn't yet hook into a risk manager. The
  executor (Step 5+ territory, or whenever the executor is wired) will
  wrap this with risk checks before forwarding signals to the venue.

---

## Step 5 — Layer-2 simulator (2026-04-14)

### Files added

```
src/mnq/sim/__init__.py                              new
src/mnq/sim/layer2/__init__.py                       new
src/mnq/sim/layer2/latency.py                        new — LatencyModel dataclass
src/mnq/sim/layer2/fills.py                          new — simulate_entry/exit_within_bar, SimulatedFill
src/mnq/sim/layer2/engine.py                         new — Layer2Engine.run, TradeRecord, TradeLedger
tests/level_1_unit/test_layer2_sim.py                new — 10 tests
```

### DoD status

- Entry-on-next-bar-open with configurable slippage — **wired**.
- Intrabar stop/TP resolution with adverse-first convention — **wired**.
- Time-stop after N bars without hit — **wired**.
- Session-end flat on last bar — **wired**.
- Determinism (same seed → same ledger) — **covered**.
- Full synthetic session (390 bars of RTH) through generated strategy →
  no exceptions, produces a non-empty ledger — **covered**.
- Level-1 green: 136 passed (was 126; +10 sim tests).

### Interpretations & 🚩 REVIEW flags

1. **🚩 REVIEW — adverse-first resolution.** When a bar's range contains
   both the stop and the target, we resolve as *stop hit first*. This is
   conservative and matches industry practice for intrabar ambiguity.
   A future upgrade could use bar_magnifier-style intrabar tick reconstruction
   from a tick feed; until then, adverse-first is the safe default.

2. **🚩 REVIEW — entry-bar-delay = 1.** Signals emitted on bar N are
   filled at bar N+1's open (the standard Pine `process_orders_on_close =
   false` convention shifted by one). This matches the `//@version=6`
   invariant `use_bar_magnifier = true` + `process_orders_on_close = false`
   in the Pine generator. If the executor's real latency (signal → WS
   order → exchange ack → fill) dominates a 1-bar delay, recalibrate.

3. **🚩 REVIEW — session-end flat.** The engine closes any open position
   at the last bar's close price with zero slippage. This is idealized
   (real closeouts hit the MOC or market auction) — the calibration
   harness in Step 7 will correct the aggregate bias.

4. **Deterministic random source.** `random.Random(seed)` is used only
   for optional rejection probability checks; the core path is
   deterministic given bars + spec. `test_determinism_two_runs` locks
   this in.

### Known residual warts

- No partial fills. A single order either fills completely at open or
  not at all. MNQ at 1 contract this is effectively always true, but
  multi-contract positions with a thin book would need modeling.
- No commission/exchange-fee accrual. The ledger tracks P&L in ticks,
  not dollars — per-contract commissions are a separate accounting
  layer handled by the gauntlet attribution in Step 8.
- Time-stop exits use the current bar's close rather than the next
  bar's open, which is a small conservatism in the bot's favor.

---

## Step 6 — MCP server (2026-04-14)

### Files added

```
src/mnq/mcp/__init__.py                              new
src/mnq/mcp/state.py                                 new — NotWiredError, ExecutorStateProvider, InMemoryExecutorState, StrategyRepository
src/mnq/mcp/tools/__init__.py                        new
src/mnq/mcp/tools/read_only.py                       new — _safe, build_read_only_tools (8 tools)
src/mnq/mcp/server.py                                new — build_server, serve_stdio, registered_tool_names
src/mnq/cli/mcp.py                                   new — `mnq mcp serve`, `mnq mcp list-tools`
tests/level_1_unit/test_mcp_server.py                new — 20 tests
```

### DoD status

- All 8 read-only tools register on `build_server()` — **verified via
  `mnq mcp list-tools`** (prints a rich table of the 8 names).
- Each tool returns structured `{ok, error, message}` on failure instead
  of raising — **covered** by unit tests.
- `NotWiredError` pre-executor-wiring surfaces as `error: "not_wired"`
  — **covered**.
- `mnq mcp serve --transport stdio` starts FastMCP — **wired**. Not
  integration-tested end-to-end (would need an MCP client in CI).
- Level-1 green: 156 passed (was 136; +20 mcp tests).

### Interpretations & 🚩 REVIEW flags

1. **🚩 REVIEW — `registered_tool_names` probes FastMCP internals.**
   Reaches into `server._tool_manager._tools`/`.list_tools()` to
   introspect. If FastMCP's API changes, update
   `src/mnq/mcp/server.py::registered_tool_names` — everything else is
   decoupled from the framework.

2. **🚩 REVIEW — InMemoryExecutorState is only a stub.** `push_state`,
   `push_fill`, etc. exist so tests can exercise the happy path, but the
   real executor (not this step) must push state into the same object.
   Two options for wiring: (a) the executor constructs the MCP server
   with its own live state object passed in, or (b) both share a
   singleton via dependency injection. Left unwired — the executor
   step is out of scope for this handoff.

3. **🚩 REVIEW — Read-only only.** Write tools (pause/flatten/cancel)
   are deferred per handoff spec. When added, they should route through
   the venue client's idempotent-cancel path and gain an auth middleware.

4. **🚩 REVIEW — `StrategyRepository` resolves `version` by id or
   semver.** Lookup tries exact `id` match, then `semver` match; raises
   `KeyError` if neither. No fuzzy matching. If a spec file is malformed,
   `StrategyRepository.list_versions()` silently skips it — might want
   to surface that as a warning for ops.

5. **Structured error envelope.** Every tool response is either
   `{ok: True, data: ...}` or `{ok: False, error: "<code>", message: "..."}`.
   The `error` codes currently in use: `not_wired`, `not_found`,
   `internal`. Document these in the MCP-client docs when that ships.

### Known residual warts

- `get_session_pnl`, `get_recent_fills`, `get_risk_utilization`,
  `get_ws_health`, `get_open_orders` all `raise NotWiredError` until the
  executor populates state. This is intentional: MCP clients should get
  a deterministic "not_wired" signal rather than stale or empty data.
- No rate-limiting or per-tool auth. Fine over `stdio` (local-only);
  will need both for the future `http` transport.
- `FastMCP.add_tool` is called with `(fn, name, description)` positional
  + keyword args. If FastMCP's signature changes, bump mcp dep minver
  and update the call site.

---

## Step 7 — Calibration harness (2026-04-14)

### Files added

```
src/mnq/calibration/__init__.py                      new
src/mnq/calibration/fit_slippage.py                  new — per-regime OLS
tests/level_1_unit/test_fit_slippage.py              new — 14 tests
```

### DoD status

- Feed 200 synthetic fills with known `a, b` → recover both within 5%
  — **covered** by `test_recovers_known_a_b_within_5pct`.
- Per-regime partitioning with min-observations threshold and pooled
  fallback — **covered** by `TestFitPerRegime`.
- Level-1 green: 170 passed (was 156; +14 calibration tests).

### Interpretations & 🚩 REVIEW flags

1. **🚩 REVIEW — regime key defaults.** Default `regime_key()` buckets on
   `(session_phase, liquidity)`:
   - **Phase**: open (09:30–10:30 ET) / mid (10:30–14:30 ET) / close
     (14:30–16:00 ET) / overnight. The ET minute-of-day is expected on
     each fill as `session_phase_minute`. If the executor ingests fills
     with UTC-only timestamps, it needs to compute this on the way in.
   - **Liquidity**: static cutoffs (< 500 = low, ≥ 2000 = high) over
     `bar_volume`. These are reasonable for MNQ 1m but per-dataset
     percentile cuts would be more robust. Left as a tunable.

2. **🚩 REVIEW — adverse-sign slippage.** The fit assumes input
   `slippage_ticks` > 0 = worse-than-intended. The executor's shadow-fill
   pipeline must encode in this convention before feeding here.

3. **🚩 REVIEW — min_observations default = 20.** Below this, a regime
   rolls into the pooled fallback only; the per-regime fit is not
   recorded. Rationale: each of the 4 phases × 4 liquidity buckets = 16
   regimes; at 200 total fills/day × 30 trading days = 6000 fills, each
   regime averages ~375 — fine. For smaller calibration windows, bump
   min_observations down or tighten the regime taxonomy.

4. **Single regressor by design.** The handoff spec is
   `slippage = a + b * atr`, so the fitter only takes `bar_atr_ticks`.
   Extending to multivariate (add quoted-spread, imbalance, volatility-
   of-volatility) is a one-line change — swap `_ols_single_regressor`
   for a `numpy.linalg.lstsq` over an X matrix — but it would invalidate
   the `5%`-recovery test's tolerances.

5. **Fallback behavior.** `SlippageModel.predict(regime, atr)` falls
   back to the pooled fit when `regime` is unseen. If the fallback is
   also missing (empty dataset), we raise `KeyError`. This matches the
   sim engine's preference for a hard failure over a silent zero.

### Known residual warts

- No HAC/robust SE yet — per-regime `SlippageFit` only carries OLS
  `r2` and sample-std residual. If the attribution module ever wants to
  propagate uncertainty through sim → metrics, we'll need to enrich
  SlippageFit with `a_se`, `b_se`.
- `fit_per_regime` iterates rows in pure Python. Fine up to ~50k fills;
  if we ever throw a million fills at it, switch to polars group_by
  aggregations.

---

## Step 8 — Attribution metrics, benchmarks, gates 13/14 (2026-04-14)

### Files added/modified

```
src/mnq/gauntlet/metrics_attribution.py              rewrite — [CONTRACT] -> [REAL]
src/mnq/gauntlet/benchmarks.py                       rewrite — [CONTRACT] -> [REAL]
src/mnq/gauntlet/gates/gate_attribution.py           rewrite — [CONTRACT] -> [REAL]
tests/level_2_property/__init__.py                   new (package marker)
tests/level_2_property/conftest.py                   new — auto-marks level2
tests/level_2_property/test_attribution.py           new — 17 tests
```

### DoD status

- Property tests in `tests/level_2_property/test_attribution.py` pass —
  **all 17 passing**.
- Pure-beta synthetic → fails g13 and g14 — **verified**.
- Constant-edge synthetic → passes both — **verified**.
- Naive-momentum-clone → fails g13 specifically on `naive_momentum`
  benchmark — **verified**.
- Level-1 + Level-2 green: 170 + 17 = 187 passed.

### Interpretations & 🚩 REVIEW flags

1. **🚩 REVIEW — statsmodels dependency not added.** The handoff says
   "reference statsmodels ... `get_robustcov_results(cov_type='HAC',
   cov_kwds={'maxlags': L})`". Adding statsmodels pulls in patsy +
   pandas (~150MB of deps). I implemented the Newey-West (1987) Bartlett-
   kernel HAC covariance directly with numpy (~30 lines,
   `_bartlett_hac_cov`). Bandwidth = `floor(4*(N/100)^(2/9))` matches NW
   1994. The HAC-vs-OLS-SE property test locks in the correct inequality.
   **If ops prefer the battle-tested statsmodels path, swap in
   `statsmodels.regression.linear_model.OLS(...).fit().get_robustcov_results(
   cov_type='HAC', cov_kwds={'maxlags': L})`** — the rest of the module
   doesn't change.

2. **🚩 REVIEW — CPCVPathResult duck-typing.** Gates accept any object
   with `.returns` (np.ndarray) and `.trades_df` (pl.DataFrame). The
   actual CPCV runner isn't built yet; this is the minimal duck. When
   the runner ships, it should expose both attrs on its per-path result
   object. Alternative: have the runner emit a `CPCVPathResult` dataclass
   — I left that shaping to Step 9+.

3. **🚩 REVIEW — naive-momentum "apples-to-apples" risk shape.** The
   naive-momentum benchmark uses the *strategy's own* `stop_dist_pts`
   and `target_dist_pts` (per contract notes). That means trades where
   the strategy sized tiny stops see tiny benchmark swings too. This is
   correct by design (isolates "was the entry signal informative" from
   "was the sizing informative") but worth being explicit about.

4. **🚩 REVIEW — benchmark point value hard-coded to MNQ.** `benchmarks.py`
   uses `POINT_VALUE_USD = 2.0` (MNQ: $2/point). If/when the gauntlet
   evaluates non-MNQ strategies, read this from `spec.instrument.point_value`.

5. **🚩 REVIEW — degenerate cases.** `alpha_with_significance`:
   - `n < 30` → all fields NaN.
   - Constant strategy → `alpha=0, se=0, t=0, p=1`.
   - Constant benchmark (cash) → collapses to one-sample t-test (HAC-
     robust SE for mean(strat)).
   These match the contract spec's "numerical hygiene" section.

6. **🚩 REVIEW — gate 13 failure-reason strings.** I reproduced the
   exact wording from the contract docstring so ops tooling can pattern-
   match. If the wording changes upstream, update both places.

7. **Sortino / Calmar / kappa edge cases.** When the downside variance
   is zero (all returns above MAR), Sortino returns `+inf` when mean > 0
   and NaN otherwise. Same convention for Calmar when max_drawdown is
   zero. This is defensive — some libs return 0, which silently makes
   "infinite reward / no risk" look bad.

### Known residual warts

- `rolling_alpha_beta` reruns the full HAC fit per window — O(N·window²).
  For typical window=30 and N=500 trades, this is ~30ms on commodity
  hardware, which is fine. If gauntlet runtime ever becomes a concern,
  cache the Cholesky of `X'X` per window.
- No annualization-factor parameter on the module surface; each caller
  passes `trades_per_year` into `information_ratio` directly. If we add
  a `MetricsConfig` dataclass later, park the annualization there.
- `mnq_intraday_returns` and `naive_momentum_returns` iterate rows in
  Python. Fine for single-CPU per-path work; when CPCV parallelism wants
  `n_cpus × n_paths × n_benchmarks = ~120` calls, consider vectorizing.

---

## Handoff close-out (2026-04-14)

All eight steps from `docs/HANDOFF_TO_COWORK.md` are now `[REAL]`.
Level-1 unit + level-2 property tests: **187 passing, 0 failing**.
Level 3+ (parity, replay, integration, chaos, soak, shadow) are scope
for future milestones per the test-pyramid ordering in the Makefile.

Not implemented by intent (handoff-enumerated non-goals):
- `src/mnq/agents/*` — deferred to post-infrastructure phase.
- Level-3 through Level-8 test suites — gated by real-data + credentials.
- README §"What's actually built right now" rewrite — belongs to the
  maintainer of that file, not the handoff worker.
