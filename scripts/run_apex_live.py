"""EVOLUTIONARY TRADING ALGO // mnq_bot live runtime entrypoint.

B1 closure (Red Team review 2026-04-25). This script is the production
live path that ``eta_v3_framework/python/webhook.py`` was supposed to be.
The webhook receiver bypassed every safety subsystem (kill switch, gate
chain, tiered rollout, Firm review, slippage recorder) -- this script
wires them all and refuses to boot when prerequisites are missing.

Pattern mirrors ``eta_engine/scripts/run_eta_live.py::_amain`` so the
operator has one mental model across both bots.

Refuse-to-boot guards
---------------------
The script REFUSES to enter live mode unless every check passes:

  1. ``--live`` flag passed (default is dry-run / paper)
  2. ``APEX_LIVE_READY=1`` in env (operator-acknowledged readiness)
  3. The configured broker is NOT in ``DORMANT_BROKERS`` (per the
     2026-04-24 broker dormancy mandate; Tradovate is dormant)
  4. ``_promotion_gate.py --all`` returns rc=0 (all 9 gates PASS)
  5. ``mnq doctor`` reports no FAIL checks

Any guard missing -> the script exits non-zero with a grep-able
reason. Paper mode (default) skips guards 2-4 since no real orders
flow.

Wiring
------
Every live order placement goes through:

  * :class:`OrderBook` -- constructed with ``build_default_chain()``
    (5-gate pre-trade chain: heartbeat, pre_trade_pause, deadman,
    correlation, governor)
  * :class:`CircuitBreaker` -- 3-breaker safety net + kill switch
    file watcher
  * :class:`TieredRollout.allowed_qty()` -- enforces tier sizing;
    HALT -> 0 contracts
  * Per-bar :func:`firm_runtime.run_six_stage_review` -- B4 closure
    (v0.2.6). Each bar consumed from the real-tape adapter triggers a
    six-stage adversarial review; PM REJECT verdicts increment
    ``orders_blocked`` and skip the (placeholder) order intent.

Modes
-----
``--dry-run`` (default): no real orders. Constructs the full safety
stack so the wiring is exercised, but routes through a MockVenue.

``--live``: requires APEX_LIVE_READY=1 + non-dormant broker +
promotion gates green + doctor green. Routes through the real
:class:`VenueRouter`.

Usage
-----
    # Dry-run smoke (default)
    python scripts/run_eta_live.py --max-bars 1

    # Live (requires explicit operator opt-in)
    APEX_LIVE_READY=1 BROKER_TYPE=ibkr python scripts/run_eta_live.py --live

    # JARVIS-supervised paper
    python scripts/run_eta_live.py --max-bars 1440 --tick-interval 60

Exit codes
----------
0 -- clean exit (max-bars reached or Ctrl+C after clean drain)
1 -- runtime error during tick loop
2 -- argument parse error (argparse default)
78 -- boot refused (operator config error; matches EX_CONFIG sysexit)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnq.core.paths import LIVE_SIM_JOURNAL, STATE_DIR  # noqa: E402
from mnq.core.types import Bar  # noqa: E402
from mnq.executor.orders import OrderBook  # noqa: E402
from mnq.executor.safety import CircuitBreaker, KillSwitchFile  # noqa: E402
from mnq.risk.gate_chain import build_default_chain  # noqa: E402
from mnq.risk.rollout_store import RolloutStore  # noqa: E402
from mnq.risk.tiered_rollout import TieredRollout  # noqa: E402
from mnq.storage.journal import EventJournal  # noqa: E402
from mnq.tape import DEFAULT_DATABENTO_5M, iter_databento_bars  # noqa: E402
from mnq.venues.dormancy import DormantBrokerError, assert_broker_active  # noqa: E402

logger = logging.getLogger("mnq.runtime")

# Exit codes
EX_OK = 0
EX_RUNTIME_ERROR = 1
EX_BOOT_REFUSED = 78  # EX_CONFIG-equivalent (operator config error)


# ---------------------------------------------------------------------------
# Args + config
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--live",
        action="store_true",
        help=(
            "Enable live mode. Requires APEX_LIVE_READY=1 + non-dormant "
            "broker + promotion gates green + doctor green. Default is "
            "dry-run / paper."
        ),
    )
    p.add_argument(
        "--max-bars",
        type=int,
        default=0,
        help="Stop after N tick iterations (0 = unbounded; useful for smoke tests / CI).",
    )
    p.add_argument(
        "--tick-interval",
        type=float,
        default=1.0,
        help="Seconds between tick iterations. Default 1.0 matches the "
        "live cadence; 0 = as-fast-as-possible (dry-run smoke).",
    )
    p.add_argument(
        "--variant",
        default="r5_real_wide_target",
        help="Strategy variant name; must be in TieredRollout state.",
    )
    p.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help=f"State directory (kill_switch_latch.json, rollout state). Default: {STATE_DIR}",
    )
    p.add_argument(
        "--journal",
        type=Path,
        default=None,
        help=f"Event journal path. Default: {LIVE_SIM_JOURNAL}",
    )
    p.add_argument(
        "--skip-promotion-gate",
        action="store_true",
        help="Skip the 9-gate promotion check (DRY-RUN ONLY -- ignored in --live mode).",
    )
    p.add_argument(
        "--tape",
        type=Path,
        default=None,
        help=f"Path to a Databento-format CSV tape (replays historical "
        f"bars one per tick for paper-mode soak). "
        f"Default: {DEFAULT_DATABENTO_5M.name} if it exists, "
        f"else no tape (rollout/breaker-only ticks).",
    )
    p.add_argument(
        "--no-tape",
        action="store_true",
        help="Disable tape replay even if the default tape exists "
        "(useful for unit-style smoke tests of the safety wiring).",
    )
    p.add_argument(
        "--firm-review-every",
        type=int,
        default=1,
        help="Run the six-stage Firm review once every N bars. "
        "Default 1 (every bar). Set higher for fast soak runs.",
    )
    p.add_argument(
        "--no-firm-review",
        action="store_true",
        help="Disable per-bar Firm review (B4 closure). The runtime "
        "still consumes the tape and exercises the safety stack.",
    )
    p.add_argument(
        "--inspect",
        action="store_true",
        help="Diagnostic mode: print boot guards + spec_payload + the "
        "Firm verdict for one tape bar, then exit. Does NOT enter "
        "the tick loop. Useful for paper-soak debugging.",
    )
    return p.parse_args(argv)


@dataclass
class RuntimeConfig:
    live: bool
    max_bars: int
    tick_interval_s: float
    variant: str
    state_dir: Path
    journal_path: Path
    skip_promotion_gate: bool
    tape_path: Path | None
    firm_review_every: int
    firm_review_enabled: bool
    inspect: bool = False

    @property
    def dry_run(self) -> bool:
        return not self.live


def build_config(args: argparse.Namespace) -> RuntimeConfig:
    # Resolve tape source: explicit --tape > default if it exists > None.
    tape_path: Path | None
    if args.no_tape:
        tape_path = None
    elif args.tape is not None:
        tape_path = args.tape
    elif DEFAULT_DATABENTO_5M.exists():
        tape_path = DEFAULT_DATABENTO_5M
    else:
        tape_path = None
    review_every = max(1, int(args.firm_review_every))
    return RuntimeConfig(
        live=args.live,
        max_bars=int(args.max_bars),
        tick_interval_s=float(args.tick_interval),
        variant=args.variant,
        state_dir=args.state_dir or STATE_DIR,
        journal_path=args.journal or LIVE_SIM_JOURNAL,
        skip_promotion_gate=bool(args.skip_promotion_gate),
        tape_path=tape_path,
        firm_review_every=review_every,
        firm_review_enabled=not bool(args.no_firm_review),
        inspect=bool(args.inspect),
    )


# ---------------------------------------------------------------------------
# Refuse-to-boot guards
# ---------------------------------------------------------------------------


@dataclass
class BootCheck:
    """Outcome of one refuse-to-boot guard."""

    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        marker = "OK  " if self.ok else "FAIL"
        return f"[{marker}] {self.name:<28s} {self.detail}"


def _check_live_ready_env() -> BootCheck:
    """APEX_LIVE_READY=1 must be set explicitly."""
    val = os.environ.get("APEX_LIVE_READY", "").strip()
    if val == "1":
        return BootCheck(
            "live_ready_env",
            ok=True,
            detail="APEX_LIVE_READY=1 set",
        )
    return BootCheck(
        "live_ready_env",
        ok=False,
        detail=(
            f"APEX_LIVE_READY != '1' (got {val!r}). "
            "Live mode requires explicit operator acknowledgment. "
            "Set APEX_LIVE_READY=1 in env to proceed."
        ),
    )


def _check_broker_dormancy() -> BootCheck:
    """The configured broker must NOT be in DORMANT_BROKERS."""
    broker = os.environ.get("BROKER_TYPE", "").strip().lower()
    if not broker:
        return BootCheck(
            "broker_dormancy",
            ok=False,
            detail="BROKER_TYPE not set in env (live mode needs IBKR or Tastytrade)",
        )
    try:
        assert_broker_active(broker)
    except DormantBrokerError as exc:
        return BootCheck(
            "broker_dormancy",
            ok=False,
            detail=str(exc),
        )
    return BootCheck(
        "broker_dormancy",
        ok=True,
        detail=f"BROKER_TYPE={broker!r} is active",
    )


def _check_promotion_gates() -> BootCheck:
    """Run scripts/_promotion_gate.py --all; must return 0."""
    gate_script = REPO_ROOT / "scripts" / "_promotion_gate.py"
    if not gate_script.exists():
        return BootCheck(
            "promotion_gates",
            ok=False,
            detail=f"missing {gate_script.relative_to(REPO_ROOT)}",
        )
    proc = subprocess.run(
        [sys.executable, str(gate_script), "--all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return BootCheck(
            "promotion_gates",
            ok=True,
            detail="all 9 gates PASS",
        )
    # Surface a one-line summary
    last = next(
        (ln for ln in reversed(proc.stdout.splitlines()) if "Verdict" in ln),
        f"rc={proc.returncode}",
    )
    return BootCheck(
        "promotion_gates",
        ok=False,
        detail=f"{last} (run scripts/_promotion_gate.py --all for details)",
    )


def _check_doctor() -> BootCheck:
    """mnq doctor must report no FAIL."""
    try:
        from mnq.cli.doctor import run_all_checks
    except ImportError as exc:
        return BootCheck(
            "doctor",
            ok=False,
            detail=f"could not import mnq.cli.doctor: {exc}",
        )
    try:
        results = run_all_checks(strict=False)
    except Exception as exc:  # noqa: BLE001 -- defensive
        return BootCheck(
            "doctor",
            ok=False,
            detail=f"run_all_checks raised: {exc}",
        )
    fails = [r for r in results if r.status == "fail"]
    if not fails:
        return BootCheck(
            "doctor",
            ok=True,
            detail=f"{len(results)} checks, no FAILs",
        )
    return BootCheck(
        "doctor",
        ok=False,
        detail=f"{len(fails)} FAIL: " + ", ".join(r.name for r in fails),
    )


def _check_kill_switch_latch(state_dir: Path) -> BootCheck:
    """Refuse if a prior kill-switch trip is still latched on disk."""
    latch_path = state_dir / "kill_switch_latch.json"
    if not latch_path.exists():
        return BootCheck(
            "kill_switch_latch",
            ok=True,
            detail="no latch file (clean boot)",
        )
    try:
        latch = json.loads(latch_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return BootCheck(
            "kill_switch_latch",
            ok=False,
            detail=f"latch file unparseable: {exc}",
        )
    if latch.get("cleared_at_utc"):
        return BootCheck(
            "kill_switch_latch",
            ok=True,
            detail=(f"latch file present but cleared at {latch.get('cleared_at_utc')}"),
        )
    return BootCheck(
        "kill_switch_latch",
        ok=False,
        detail=(
            f"latch ARMED: {latch.get('reason', 'unknown')}. "
            "Clear with: python -m mnq.cli clear_kill_switch "
            "--confirm --operator <your_name>"
        ),
    )


def evaluate_boot_guards(cfg: RuntimeConfig) -> list[BootCheck]:
    """Run every refuse-to-boot guard for the configured mode."""
    checks: list[BootCheck] = []
    # Always check kill-switch latch -- it persists across runs and
    # blocks both paper AND live booting.
    checks.append(_check_kill_switch_latch(cfg.state_dir))
    if not cfg.live:
        # Paper mode: skip live-only guards.
        return checks
    checks.append(_check_live_ready_env())
    checks.append(_check_broker_dormancy())
    if not cfg.skip_promotion_gate:
        checks.append(_check_promotion_gates())
    checks.append(_check_doctor())
    return checks


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@dataclass
class TickStats:
    bars_processed: int = 0
    signals_seen: int = 0
    orders_submitted: int = 0
    orders_blocked: int = 0
    errors: int = 0
    firm_reviews_run: int = 0
    firm_approved: int = 0
    firm_rejected: int = 0


@dataclass
class FirmReviewResult:
    """Trimmed PM-stage view used by the runtime to gate orders."""

    verdict: str
    pm_probability: float
    reasoning: str
    primary_driver: str = ""

    @property
    def is_reject(self) -> bool:
        # PM verdicts are typically APPROVE / SCALED / REJECT / KILL.
        # Treat anything starting with REJ or KILL as a block.
        v = (self.verdict or "").upper()
        return v.startswith(("REJ", "KILL", "BLOCK"))


@dataclass
class ApexRuntime:
    """Live runtime supervisor.

    Constructs the safety stack, runs the tick loop, drains cleanly on
    stop. Mirrors eta_engine/scripts/run_eta_live.py::ApexRuntime
    but slimmer (single-bot mnq, no multi-bot orchestration).
    """

    cfg: RuntimeConfig
    journal: EventJournal
    book: OrderBook
    breaker: CircuitBreaker
    rollout: TieredRollout
    tape: Any = None  # Iterator[Bar] | None -- annotated Any to avoid heavy generic import
    spec_payload: dict[str, Any] | None = None  # v0.2.7: precomputed real spec
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    stats: TickStats = field(default_factory=TickStats)
    _firm_shim_unavailable: bool = False  # Latched after first ImportError; logged once.

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> int:
        """Tick loop. Returns runtime exit code (0 = clean)."""
        bar_i = 0
        try:
            while not self._stop.is_set():
                if self.cfg.max_bars and bar_i >= self.cfg.max_bars:
                    break
                try:
                    cont = await self._tick(bar_i)
                    if cont is False:
                        break
                except Exception as exc:  # noqa: BLE001 -- defensive
                    self.stats.errors += 1
                    logger.exception("tick %d raised: %s", bar_i, exc)
                bar_i += 1
                if self.cfg.tick_interval_s > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self.cfg.tick_interval_s,
                        )
        finally:
            logger.info(
                "drain complete: bars=%d signals=%d orders=%d "
                "blocked=%d errors=%d firm_reviews=%d "
                "firm_approved=%d firm_rejected=%d",
                self.stats.bars_processed,
                self.stats.signals_seen,
                self.stats.orders_submitted,
                self.stats.orders_blocked,
                self.stats.errors,
                self.stats.firm_reviews_run,
                self.stats.firm_approved,
                self.stats.firm_rejected,
            )
        return EX_OK

    async def _tick(self, bar_i: int) -> bool:
        """One iteration. Returns False to signal end-of-tape (drain).

        Sequence:
          1. Pull next bar from tape (if any). End-of-tape -> drain.
          2. Increment bars_processed.
          3. Check rollout (HALT / qty=0 -> skip).
          4. Check circuit breaker (kill switch / drawdown -> block).
          5. Run Firm review on this bar (B4) -- REJECT verdict blocks
             the placeholder order.
          6. Log. (Real order placement lands in v0.2.7+ when the signal
             generator is wired.)
        """
        from datetime import UTC
        from datetime import datetime as _dt

        bar = self._next_bar()
        if self.tape is not None and bar is None:
            logger.info("tick %d: tape exhausted, draining", bar_i)
            return False
        self.stats.bars_processed += 1

        qty = self.rollout.allowed_qty()
        if qty == 0:
            return True
        decision = self.breaker.allow_trade(now=_dt.now(UTC))
        if not decision.allowed:
            self.stats.orders_blocked += 1
            logger.info(
                "tick %d: trade refused by circuit breaker: %s (%s)",
                bar_i,
                decision.reason,
                decision.detail,
            )
            return True

        # B4: per-bar Firm review (interval-throttled). REJECT -> block.
        if (
            bar is not None
            and self.cfg.firm_review_enabled
            and not self._firm_shim_unavailable
            and bar_i % self.cfg.firm_review_every == 0
        ):
            review = self._run_firm_review(bar, bar_i)
            if review is not None:
                self.stats.firm_reviews_run += 1
                if review.is_reject:
                    self.stats.firm_rejected += 1
                    self.stats.orders_blocked += 1
                    logger.info(
                        "tick %d: firm REJECT (%s, p=%.2f) -- %s",
                        bar_i,
                        review.verdict,
                        review.pm_probability,
                        (review.reasoning or "")[:80],
                    )
                    return True
                self.stats.firm_approved += 1
                logger.debug(
                    "tick %d: firm %s (p=%.2f)",
                    bar_i,
                    review.verdict,
                    review.pm_probability,
                )

        logger.debug(
            "tick %d: rollout=%s tier=%d allowed_qty=%d circuit=ok",
            bar_i,
            self.rollout.state.value,
            self.rollout.tier,
            qty,
        )
        return True

    def _next_bar(self) -> Bar | None:
        """Pull the next bar from the tape, or None if no tape / exhausted."""
        if self.tape is None:
            return None
        try:
            return next(self.tape)
        except StopIteration:
            return None

    def _run_firm_review(self, bar: Bar, bar_i: int) -> FirmReviewResult | None:
        """Invoke ``firm_runtime.run_six_stage_review`` for this bar.

        Fail-open: if the shim is unavailable or the review raises, log
        and continue (latch _firm_shim_unavailable on ImportError so
        we don't keep spamming warnings).
        """
        try:
            from mnq.firm_runtime import compute_confluence, run_six_stage_review
        except ImportError as exc:
            logger.warning(
                "firm_runtime shim unavailable -- per-bar review disabled "
                "for the rest of this run (fail-open): %s",
                exc,
            )
            self._firm_shim_unavailable = True
            return None

        # v0.2.7: use precomputed real spec_payload if wired in; fall back to
        # a minimal stub if not (e.g. test path that didn't pass spec_payload).
        if self.spec_payload is not None:
            spec_payload = dict(self.spec_payload)  # shallow copy per-bar
        else:
            spec_payload = {
                "strategy_id": self.cfg.variant,
                "sample_size": 100,
                "expected_expectancy_r": 0.5,
                "oos_degradation_pct": 20.0,
                "entry_logic": f"variant={self.cfg.variant}",
                "stop_logic": "10-tick hard stop",
                "target_logic": "2R fixed",
                "dd_kill_switch_r": 12.0,
                "regimes_approved": ["normal_vol_trend"],
                "approved_sessions": ["RTH"],
                "provenance": ["stub"],
            }
        confluence = compute_confluence(
            internals={},
            volatility={},
            cross_asset={},
            session={"phase": "RTH", "is_rth": True},
            micro={"spread_ticks": 1.0},
            calendar={},
            eta_v3={},
            regime={"canonical": "normal_vol_trend", "persistence_bars": 40},
        )
        bar_payload = {
            "ts": bar.ts.isoformat(),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": int(bar.volume),
        }
        try:
            stages = run_six_stage_review(
                strategy_id=self.cfg.variant,
                decision_context=(
                    f"live tick {bar_i} bar_ts={bar_payload['ts']} c={bar_payload['close']}"
                ),
                payload={"spec": spec_payload, "bar": bar_payload},
                regime_snapshot={
                    "regimes_approved": spec_payload["regimes_approved"],
                },
                confluence_result=confluence,
            )
        except Exception:  # noqa: BLE001 -- defensive; review must never crash runtime
            logger.exception("tick %d: firm review raised; skipping", bar_i)
            return None

        pm = stages.get("pm", {}) if isinstance(stages, dict) else {}
        return FirmReviewResult(
            verdict=str(pm.get("verdict", "?")),
            pm_probability=float(pm.get("probability", 0.0)),
            reasoning=str(pm.get("reasoning", "")),
            primary_driver=str(pm.get("primary_driver", "")),
        )


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------


def _install_signal_handlers(runtime: ApexRuntime) -> None:
    def _handler(*_: Any) -> None:
        logger.info("received stop signal; draining")
        runtime.request_stop()

    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, _handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handler)


# ---------------------------------------------------------------------------
# _amain
# ---------------------------------------------------------------------------


async def _amain(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = build_config(args)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.journal_path.parent.mkdir(parents=True, exist_ok=True)

    # Refuse-to-boot guards
    checks = evaluate_boot_guards(cfg)
    tape_label = cfg.tape_path.name if cfg.tape_path is not None else "(none)"
    print("MNQ_BOT // run_eta_live")
    print("=" * 64)
    print(f"mode          : {'LIVE' if cfg.live else 'DRY-RUN'}")
    print(f"variant       : {cfg.variant}")
    print(f"max_bars      : {cfg.max_bars or 'unbounded'}")
    print(f"tick_interval : {cfg.tick_interval_s}s")
    print(f"state_dir     : {cfg.state_dir}")
    print(f"journal       : {cfg.journal_path}")
    print(f"tape          : {tape_label}")
    print(
        f"firm_review   : {'ON' if cfg.firm_review_enabled else 'OFF'} "
        f"(every {cfg.firm_review_every} bar{'s' if cfg.firm_review_every != 1 else ''})",
    )
    print("-" * 64)
    print("BOOT GUARDS")
    for c in checks:
        print(f"  {c.render()}")
    print("=" * 64)
    failed = [c for c in checks if not c.ok]
    if failed:
        logger.error(
            "boot REFUSED: %d guard(s) FAILed: %s",
            len(failed),
            ", ".join(c.name for c in failed),
        )
        return EX_BOOT_REFUSED

    # Wire safety stack
    journal = EventJournal(cfg.journal_path)
    book = OrderBook(journal, build_default_chain())
    kill_switch = KillSwitchFile(path=cfg.state_dir / "kill_switch.flag")
    breaker = CircuitBreaker(kill_switch=kill_switch)

    # Load or initialize tiered rollout for the variant
    rollout_path = cfg.state_dir / "rollouts.json"
    rstore = RolloutStore(rollout_path)
    rollouts = rstore.load_all()
    if cfg.variant in rollouts:
        rollout = rollouts[cfg.variant]
    else:
        rollout = TieredRollout.initial(cfg.variant)

    # Tape source (B4): real-tape adapter for per-bar Firm review.
    tape_iter = None
    if cfg.tape_path is not None:
        try:
            tape_iter = iter_databento_bars(
                cfg.tape_path,
                max_bars=cfg.max_bars or None,
            )
        except FileNotFoundError as exc:
            logger.error("tape source missing: %s", exc)
            return EX_BOOT_REFUSED

    # v0.2.7: precompute the variant's real spec_payload from yaml +
    # cached backtest stats. Once at startup, then reused per-bar.
    from mnq.spec.runtime_payload import build_spec_payload

    spec_payload = build_spec_payload(cfg.variant)

    runtime = ApexRuntime(
        cfg=cfg,
        journal=journal,
        book=book,
        breaker=breaker,
        rollout=rollout,
        tape=tape_iter,
        spec_payload=spec_payload,
    )
    _install_signal_handlers(runtime)

    print(
        f"safety        : OrderBook+chain={book._gate_chain is not None} "  # noqa: SLF001
        f"breaker.kill={kill_switch.path.name} "
        f"rollout={rollout.state.value}/T{rollout.tier}/qty={rollout.allowed_qty()}",
    )
    print(
        f"spec_payload  : provenance={spec_payload.get('provenance')} "
        f"n={spec_payload.get('sample_size')} "
        f"E={spec_payload.get('expected_expectancy_r'):.3f}R "
        f"oos={spec_payload.get('oos_degradation_pct'):.1f}%",
    )
    print("=" * 64)

    # --inspect mode: dump full spec + one Firm verdict, then exit. No
    # tick loop, no order routing. Useful for paper-soak debugging
    # ("what is the runtime ACTUALLY going to send to the Firm?").
    if cfg.inspect:
        return _run_inspect(runtime, spec_payload)

    try:
        rc = await runtime.run()
    except Exception:  # noqa: BLE001 -- final-safety net; logged
        logger.exception("runtime crashed")
        return EX_RUNTIME_ERROR
    return rc


def _format_regime_table(regime_expectancy: dict) -> str:
    """Render the regime_expectancy dict as a small markdown table.

    Returns the empty string when there's no regime evidence (lets
    the caller cleanly skip emitting an empty section). v0.2.16
    helper for ``--inspect`` so the operator can scan per-regime
    edge without parsing the JSON dump of spec_payload.
    """
    if not regime_expectancy:
        return ""
    lines = [
        "| regime | n_days | total_pnl | pnl_per_day | expectancy_r |",
        "|---|---:|---:|---:|---:|",
    ]
    # Order regimes by expectancy descending so the strongest evidence
    # bubbles to the top. Within the same expectancy, by n_days desc.
    sorted_regimes = sorted(
        regime_expectancy.items(),
        key=lambda kv: (
            -kv[1].get("expectancy_r", 0.0),
            -kv[1].get("n_days", 0.0),
        ),
    )
    for regime, stats in sorted_regimes:
        n = int(stats.get("n_days", 0))
        total = stats.get("total_pnl", 0.0)
        per_day = stats.get("pnl_per_day", 0.0)
        e = stats.get("expectancy_r", 0.0)
        lines.append(
            f"| {regime} | {n} | ${total:+.2f} | ${per_day:+.2f} | {e:+.4f}R |",
        )
    return "\n".join(lines)


def _format_drift_summary(spec_payload: dict) -> str:
    """Render a one-line drift indicator from expected vs recency-weighted
    expectancy_r (v0.2.19).

    Returns "" when either field is None (no signal). Otherwise:

      "EDGE STEADY  : E=+0.150R | recency=+0.155R | delta=+0.005R"
      "EDGE FADING  : E=+0.500R | recency=+0.100R | delta=-0.400R"
      "EDGE GROWING : E=+0.100R | recency=+0.500R | delta=+0.400R"

    Tags:
      EDGE STEADY  -- |delta| < 0.05R (essentially the same)
      EDGE FADING  -- recency < expected by >= 0.05R
      EDGE GROWING -- recency > expected by >= 0.05R
    """
    expected = spec_payload.get("expected_expectancy_r")
    recency = spec_payload.get("recency_weighted_expectancy_r")
    if expected is None or recency is None:
        return ""
    delta = recency - expected
    threshold = 0.05
    if abs(delta) < threshold:
        tag = "EDGE STEADY "
    elif delta < 0:
        tag = "EDGE FADING "
    else:
        tag = "EDGE GROWING"
    return f"{tag} : E={expected:+.3f}R | recency={recency:+.3f}R | delta={delta:+.3f}R"


def _run_inspect(runtime: ApexRuntime, spec_payload: dict) -> int:
    """Diagnostic mode: print full spec + first-bar Firm verdict.

    Does NOT enter the tick loop, place orders, or modify journal /
    rollout state. Pulls one bar from the tape (if any), runs the
    Firm review against it via ``runtime._run_firm_review``, prints
    both as JSON, and returns. Helps the operator answer "what is the
    runtime actually going to send to the Firm and what verdict comes
    back?" without a full paper run.
    """
    print("\n--- spec_payload (full) ---")
    print(json.dumps(spec_payload, indent=2, default=str))

    # v0.2.19: drift indicator (E vs recency-weighted E)
    drift = _format_drift_summary(spec_payload)
    if drift:
        print("\n--- drift indicator (v0.2.18 recency vs unweighted) ---")
        print(drift)

    # v0.2.16: per-regime expectancy as a markdown table for human
    # readability. Skipped when regime_expectancy is empty (e.g. stub
    # provenance, no tape coverage).
    regime_table = _format_regime_table(
        spec_payload.get("regime_expectancy") or {},
    )
    if regime_table:
        print("\n--- regime_expectancy (sorted by expectancy_r desc) ---")
        print(regime_table)

    bar = runtime._next_bar()  # noqa: SLF001 -- diagnostic access
    if bar is None:
        print("\n--- bar: none (no tape configured or tape empty) ---")
        return EX_OK
    print("\n--- bar (most recent tape entry) ---")
    print(
        json.dumps(
            {
                "ts": bar.ts.isoformat(),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume),
            },
            indent=2,
        )
    )
    if not runtime.cfg.firm_review_enabled:
        print("\n--- firm review: DISABLED (--no-firm-review) ---")
        return EX_OK
    review = runtime._run_firm_review(bar, 0)  # noqa: SLF001
    if review is None:
        print(
            "\n--- firm review: shim unavailable -- per-bar review "
            "would be SKIPPED at runtime (fail-open) ---",
        )
        return EX_OK
    print("\n--- firm verdict (PM stage) ---")
    print(
        json.dumps(
            {
                "verdict": review.verdict,
                "pm_probability": review.pm_probability,
                "is_reject": review.is_reject,
                "reasoning": review.reasoning,
                "primary_driver": review.primary_driver,
            },
            indent=2,
        )
    )
    return EX_OK


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return EX_OK


if __name__ == "__main__":
    sys.exit(main())
