# Red Team Review — mnq_bot EVOLUTIONARY TRADING ALGO v3 framework

**Date:** 2026-04-25
**Reviewer:** `risk-advocate` agent (Opus 4.7, adversarial posture)
**Verdict:** **BLOCKED** — 5 BLOCKERs + 4 HIGHs + 7 process gaps
**Scope:** Live-promotion readiness of `mnq_bot` for the Apex 50K eval

---

## TL;DR

The mandatory Red Team dissent process (per the Firm protocol) found that
**no real money should flow through `mnq_bot` as currently configured**.
The "live" path is `eta_v3_framework/python/webhook.py`, a Flask receiver
that bypasses every safety subsystem in `src/mnq/`: kill switch, gate
chain, tiered rollout, Firm six-stage review, slippage recorder. Multiple
components silently fail-open, including a journal-path mismatch that
means the daily-trade-cap gate is reading a 0-byte file.

Five BLOCKERs are listed below. Three are **design calls** that need
operator decisions (B1, B3, B4). Two are **mechanical** and can be closed
without architectural changes (B5 dormancy enforcement, plus parts of
B2 path coherence). Of the four HIGHs, one is mechanical (H1 spec audit)
and three need design input (H2 shim source, H3 tiered-rollout wiring,
H4 orchestrator gate-blocking).

---

## Verdict matrix

| ID | Severity | Title | Disposition | Owner |
|----|---------|-------|-------------|-------|
| B1 | BLOCKER | No production live entrypoint exists; webhook.py bypasses safety subsystems | DESIGN CALL | operator |
| B2 | BLOCKER | Journal-path mismatch — gate chain reads 0-byte file | mechanical (doctor check) + design (path consolidation) | operator + Claude |
| B3 | BLOCKER | `OrderBook(journal)` defaults `gate_chain=None` — silent-disable | DESIGN CALL (breaking API change) | operator |
| B4 | BLOCKER | Firm six-stage review is dead code in live path; runs on synthetic bar only | DESIGN CALL | operator |
| B5 | BLOCKER | Broker dormancy mandate not enforced — Tradovate is the only venue but is DORMANT | MECHANICAL | Claude |
| H1 | HIGH | Spec → code drift; v2.2 paper config doesn't exist anywhere in repo | MECHANICAL (spec audit) | Claude |
| H2 | HIGH | `_shim_guard` self-heal has no source to copy from | DESIGN CALL (port firm out of OneDrive) | operator |
| H3 | HIGH | Phase-9 tiered rollout state machine never consulted by order-placing code | DESIGN CALL (depends on B1) | operator |
| H4 | HIGH | `run_all_phases.py` non-blocking on every gate; rc=1 with `n_fail > 0` is the default | MECHANICAL + DESIGN | mixed |

**Process gaps:** 7 (analogous to eta_engine review).

---

## Why this differs from the eta_engine R1 review

The `eta_engine` R1 review (closed in v0.1.64 - v0.1.69) found similar
shapes — production wire-up gaps, alert routing gaps, aggregate-equity
invariants — but the BLOCKERs there were *narrow* (a single `_amain`
constructor missing two kwargs, seven event names missing from
`alerts.yaml`). They were closable in days, and the existing observation
infrastructure mostly worked.

This review is different. The structural problem in `mnq_bot` is that the
"live" path **doesn't exist** — what does exist is a webhook receiver that
predates the safety subsystems and was never refactored to use them. The
mechanical fixes alone can't close this; the operator needs to make a
call on whether to:

  (a) refactor `webhook.py` to delegate to `mnq.executor.venue_router.VenueRouter`
      with full gate-chain wiring, OR
  (b) delete `webhook.py` and build a new live entrypoint that mirrors
      `eta_engine/scripts/run_eta_live.py::_amain`, OR
  (c) document `mnq_bot` as paper-only-until-vN.M and refuse to boot any
      live path.

Option (c) is the lowest-risk path and what this review recommends as the
default until the operator explicitly chooses (a) or (b).

---

## Findings (full text from risk-advocate)

### B1 — No production live entrypoint; webhook.py bypasses safety

**Severity:** BLOCKER
**Disposition:** DESIGN CALL (operator)

There is no `scripts/run_eta_live.py` or equivalent. The `mnq` CLI
(`src/mnq/cli/main.py:23-27`) registers `venue`, `spec`, `mcp`, `doctor`,
`parity` — no `live`, no `run`. The only code path that POSTs an order to
a broker is `eta_v3_framework/python/webhook.py:118-141`, which:

- Forwards to `BROKER_URL` (env) with `BROKER_API_KEY` (env) — generic
  `requests.post`, not the venue adapter pattern.
- Calls **none** of: `mnq.executor.safety.CircuitBreaker`,
  `mnq.risk.gate_chain.build_default_chain`,
  `mnq.risk.tiered_rollout.TieredRollout.allowed_qty()`,
  `mnq.firm_runtime.run_six_stage_review`,
  `mnq.executor.orders.OrderBook`,
  `mnq.calibration.recorder.SlippageRecorder`,
  `mnq.observability.parity.summarize_env`.
- Only "guard" is `double_check_firm()` (one threshold + crisis lockdown).
- `DRY_RUN=true` default but flippable via env. No code path in
  `mnq.executor` knows the bot went live.

**Cost of inaction:** Apex 50K eval bot blows up on a runaway loss streak
that no installed gate can stop.

**Exit criteria (Lands when):** The operator picks one of:
  (a) Refactor `webhook.py` to delegate to
      `mnq.executor.venue_router.VenueRouter`, AND `webhook.py` constructs
      `OrderBook(journal, gate_chain=build_default_chain())`,
      `CircuitBreaker(... kill_switch=KillSwitchFile(path=...))`,
      `TieredRollout` with non-zero `allowed_qty()`, AND
      `SlippageRecorder` records every order; OR
  (b) Delete `webhook.py` and build `scripts/run_eta_live.py` mirroring
      `eta_engine/scripts/run_eta_live.py::_amain`; OR
  (c) Mark `mnq_bot` as paper-only via `doctor` check that refuses live
      mode and document in ROADMAP.md.

---

### B2 — Journal-path mismatch silently allows every governor breach

**Severity:** BLOCKER
**Disposition:** mechanical (doctor check) + design (path consolidation)

`src/mnq/risk/gate_chain.py:106` defines
`JOURNAL_PATH = DATA_ROOT / "live_sim" / "journal.sqlite"` →
`C:\Users\edwar\projects\mnq_bot\data\live_sim\journal.sqlite` (0 bytes,
last touched April 16). Meanwhile `scripts/live_sim.py:937` writes to
`/sessions/kind-keen-faraday/data/live_sim` — a Linux-sandbox absolute
path interpreted on Windows as `C:\sessions\kind-keen-faraday\...` (241KB,
written today). **Two different files. Live writes go to one, gate-chain
reads the other.**

`governor_gate` (`gate_chain.py:219-234`) sums `pnl.update` events from the
empty file → returns `(n=0, streak=0, pnl=0.0)` → ALLOW for every trade.

17 scripts have hardcoded `/sessions/kind-keen-faraday/...` paths.

**Exit criteria (Lands when):**
  1. Single `mnq.core.paths.JOURNAL_PATH` constant resolved against
     `REPO_ROOT` replaces all hardcoded sandbox paths. CI test asserts
     no `/sessions/` references in `scripts/`.
  2. `mnq doctor` gains a `_check_journal_paths_aligned()` step that
     fails when any `JOURNAL_PATH` constant in `src/mnq/` resolves to a
     path different from what `live_sim.py` writes to.
  3. Pre-commit hook rejects new `/sessions/...` paths.

---

### B3 — `OrderBook.gate_chain` defaults to None (silent-disable)

**Severity:** BLOCKER
**Disposition:** DESIGN CALL (breaking API change)

`src/mnq/executor/orders.py:157-182`:
```python
def __init__(self, journal, *, logger=None, gate_chain: Any = None) -> None:
```
Docstring: "Default ``None`` preserves legacy (ungated) behavior — every
existing test case stays green." `live_sim.py:364` constructs
`OrderBook(journal)` (no `gate_chain`); `shadow_trader.py:172` likewise.

The unit test `test_executor_gate_chain.py:33-42`
(`test_no_chain_allows_normally`) actively asserts that `OrderBook(journal)`
lets all orders through — protecting the silent-disable bug.

**Exit criteria (Lands when):**
  1. `gate_chain` becomes a required positional argument.
  2. Explicit `OrderBook.unsafe_no_gate_chain(journal)` factory exists for
     tests that need it.
  3. `test_no_chain_allows_normally` is replaced with
     `test_no_chain_raises_without_explicit_unsafe_factory`.
  4. A Phase-E sanity stage in `run_all_phases.py` asserts `OrderBook`
     cannot be constructed without a chain in `mode=production` env.

---

### B4 — Firm review is dead code; runs on a hardcoded synthetic bar

**Severity:** BLOCKER
**Disposition:** DESIGN CALL

Per-bar gating by the six-stage Firm review is the bot's central premise.
Reality:
- `run_six_stage_review` only called from `scripts/firm_live_review.py:256`
  (one-shot reporter) and one test.
- `firm_live_review.py:158-181` calls it once per variant against a
  HARDCODED synthetic bar (open=21000.0, fixed VIX=18, fixed cum_delta).
- `live_sim.py:337` imports `record_trade_outcome` from `mnq.firm_runtime`,
  but that name doesn't exist in the shim. Wrapped in
  `except (ImportError, Exception): pass` — adaptive learner
  integration ALWAYS fails silently.
- `webhook.py` (the real live path) does not import `firm_runtime` at all.
- The shim hard-codes
  `_FIRM_PACKAGE_PARENT = Path('C:\\Users\\edwar\\OneDrive\\The_Firm\\the_firm_complete\\desktop_app')`
  — bidirectional OneDrive coupling that the 2026-04-17 migration was
  supposed to eliminate.

**Exit criteria (Lands when):**
  1. `firm_bridge.py` contract probe asserts `record_trade_outcome` (and
     every other live-path import) exists in the generated shim.
  2. `firm_live_review.py` runs against the actual bar tape of recent
     trades, not a synthetic bar; OR the file is renamed to remove the
     `_live` suffix.
  3. `run_six_stage_review` is wired into the entry decision in whatever
     real live path replaces `webhook.py` (depends on B1 closure).
  4. `_FIRM_PACKAGE_PARENT` is moved out of OneDrive
     (e.g. `C:\Users\edwar\projects\firm\`).

---

### B5 — Broker dormancy mandate not enforced; Tradovate is the only venue

**Severity:** BLOCKER
**Disposition:** **MECHANICAL** — closable autonomously

Per CLAUDE.md mandate (2026-04-24), Tradovate is DORMANT and IBKR +
Tastytrade are active. In `mnq_bot`:
- `src/mnq/venues/` contains only `tradovate/` and `ninjatrader.py`. No
  `ibkr/`, no `tastytrade/`.
- No `DORMANT_BROKERS` set, no router with dormancy enforcement.
- `eta_v3_framework/live_deployment/live_config.example.yaml:34`
  advertises `broker.type: "ibkr"` — phantom option, no adapter exists.
- `mnq doctor` does not check broker dormancy.

**Exit criteria (Lands when):** see "Mechanical closures" below — closed
in this commit by porting the doctor check.

---

### H1 — Spec → code drift; v2.2 paper config doesn't exist in repo

**Severity:** HIGH
**Disposition:** **MECHANICAL** — closable autonomously

Operator's stated v2.2 paper config (`confluence=5.30, atr_floor_ratio=1.0,
dow_blacklist=[3], regime_overlay=trending_only, max_trades=4/day,
stop=2R, target=3R`) does not appear anywhere in the codebase.
`grep "5\.30\|confluence_threshold\|atr_floor_ratio\|dow_blacklist"`
returns no matches. `specs/strategies/v0_1_baseline.yaml` ships
disagreeing values (risk_per_trade_usd: 50 vs operator's $25;
max_consecutive_losses: 3 vs ops_safety's 5).

The variants the live path cares about (`r5_real_wide_target`,
`t16_r5_long_only`, `t17_r5_short_only`) live as Python literals in
`scripts/strategy_v2.py:323-378` and have no spec hash.

There is no `_audit_spec_vs_code.py` equivalent to `eta_engine`'s
`_audit_roadmap_vs_code.py`.

**Exit criteria (Lands when):** see "Mechanical closures" below.

---

### H2 — `_shim_guard` self-healing has no source to copy from

**Severity:** HIGH
**Disposition:** DESIGN CALL

`src/mnq/_shim_guard.py:70-83` searches for
`upload_bundle/firm-basement-audit/fixes/firm_runtime.py` in three
candidate locations. None exist on this filesystem. `ensure_firm_runtime_healthy`
detects truncation but has no source to heal from; the conftest sets
`raise_on_failure=False` so a truncated shim during pytest collection
fails silently.

**Exit criteria (Lands when):**
  1. Either ship a known-good `firm_runtime.py` under
     `upload_bundle/firm-basement-audit/fixes/` (committed to git, tested
     by CI), OR
  2. Move the firm package out of OneDrive (eliminating the truncation
     source) and DELETE the self-healing path entirely.

---

### H3 — Tiered rollout state machine never consulted by live path

**Severity:** HIGH
**Disposition:** DESIGN CALL (depends on B1 closure)

`src/mnq/risk/tiered_rollout.py` ships TIER_0..TIER_N state machine.
`grep "allowed_qty"` across `scripts/live_sim.py` returns ZERO matches.
`webhook.py:122` reads `int(os.environ.get("APEX_QTY", "1"))` — ignores
the rollout. Promotion pipeline produces a green report but the bot can
be sized at TIER_3 even when the state machine says HALTED.

**Exit criteria (Lands when):** Whatever live entrypoint replaces
webhook.py loads `RolloutStore.load_all()` at boot AND, per-signal,
clamps `qty = min(signal.qty, rollouts[variant].allowed_qty())`. Test
asserts the constraint binds.

---

### H4 — `run_all_phases.py` non-blocking on every promotion gate

**Severity:** HIGH
**Disposition:** mechanical + design

`scripts/run_all_phases.py:228-376` flags only two stages as
`blocking=True`: `live_sim` and `replay_journal`. Every other stage
(gauntlet_check, parity_harness, gate_chain_check, hard_gate_attribution,
firm_live_review) is non-blocking. A run can have multiple DENY/FAIL
verdicts and the orchestrator returns rc=1 with `blocking_fail=False`
— any `is_paper_promoted` check that reads `rc==0 OR n_fail==0` gets
this wrong.

The 9 promotion gates (`docs/next_data_checkpoint.md`) are not realized
as blocking stages in `run_all_phases.py`. `grep "9 gates|9-gate|nine.gate"`
returns 0 hits across the repo.

**Exit criteria (Lands when):**
  1. The 9 promotion gates are encoded as `Stage(blocking=True)` set in
     `run_all_phases.py`, with explicit pass-fail criteria readable from a
     JSON status file.
  2. A final `promotion_verdict` stage aggregates all 9 gates and returns
     rc=0 ONLY if every gate is GREEN.
  3. `gate_chain_check.py` and `gauntlet_check.py` return non-zero on
     DENY when run in `--enforce` mode.

---

## Mechanical closures shipped this commit

### B5 closure — DORMANT_BROKERS doctor check

Ported pattern from `eta_engine/venues/router.py`. New file:
`src/mnq/venues/dormancy.py` defining
`DORMANT_BROKERS = frozenset({"tradovate"})`. New doctor check
`_check_broker_dormancy()` in `src/mnq/cli/doctor.py` that reads the
configured execution broker and fails RED if it's in `DORMANT_BROKERS`.

This makes the operator-mandated dormancy mechanically enforced rather
than documentation-only.

### H1 closure — spec audit script

New `scripts/_audit_spec_vs_code.py` walks
`scripts/strategy_v2.py` for `StrategyConfig(...)` literal blocks and
asserts each variant has a corresponding entry in `specs/strategies/`.
Fails when any operationally-active variant is unbacked. Analogous to
`eta_engine`'s `_audit_roadmap_vs_code.py`.

---

## Process gaps (analogous to eta_engine review)

1. **No production wire-up smoke test** (PG#3) — the unit test
   `test_no_chain_allows_normally` actively protects the silent-disable
   bug (B3).
2. **No spec-vs-code reconciler** (PG#4) — closed in this commit.
3. **No event-routing test** (PG#1) — `scripts/alerting.py` is a CLI, not
   a dispatcher; no registry to test. If Discord/Slack alerting on real
   events ships, build the registry first.
4. **No journal-path coherence test** — Doctor doesn't check (B2).
5. **No broker-dormancy doctor check** — closed in this commit (B5).
6. **No exit-criteria pinning on deferred work** (PG#2) — ROADMAP.md is
   narrative; no `v0.2.x | deferred to YYYY-MM-DD` markers per item.
7. **17 hardcoded sandbox paths** (`/sessions/kind-keen-faraday/...`) in
   `scripts/` — should never have been merged. Pre-commit hook should
   reject any new occurrence.

---

## Operator action required

To clear the BLOCKED verdict, the operator must:

1. **Pick a B1 disposition:** refactor webhook, build new live entrypoint,
   or document mnq_bot as paper-only until vN.M.
2. **Decide on B3 breaking change:** make `gate_chain` required positional?
3. **Decide on B4 wiring:** per-bar Firm review on real tape, OR remove
   `_live` from `firm_live_review.py` filename and accept it's a reporter
   not a gate?
4. **Decide on H2 source-of-truth:** ship known-good fixtures, OR
   port firm out of OneDrive?

Until these are resolved, **no real money should flow through this
bot.** The mechanical closures in this commit (B5, H1) reduce the attack
surface but do not change the BLOCKED verdict.
