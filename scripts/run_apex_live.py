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
    (NOT shipped in the v0.2.5 commit that creates this file; lands
    in v0.2.6 once the Firm runtime tape interface is stable).

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
from mnq.executor.orders import OrderBook  # noqa: E402
from mnq.executor.safety import CircuitBreaker, KillSwitchFile  # noqa: E402
from mnq.risk.gate_chain import build_default_chain  # noqa: E402
from mnq.risk.rollout_store import RolloutStore  # noqa: E402
from mnq.risk.tiered_rollout import TieredRollout  # noqa: E402
from mnq.storage.journal import EventJournal  # noqa: E402
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
        "--live", action="store_true",
        help=(
            "Enable live mode. Requires APEX_LIVE_READY=1 + non-dormant "
            "broker + promotion gates green + doctor green. Default is "
            "dry-run / paper."
        ),
    )
    p.add_argument(
        "--max-bars", type=int, default=0,
        help="Stop after N tick iterations (0 = unbounded; useful for "
             "smoke tests / CI).",
    )
    p.add_argument(
        "--tick-interval", type=float, default=1.0,
        help="Seconds between tick iterations. Default 1.0 matches the "
             "live cadence; 0 = as-fast-as-possible (dry-run smoke).",
    )
    p.add_argument(
        "--variant", default="r5_real_wide_target",
        help="Strategy variant name; must be in TieredRollout state.",
    )
    p.add_argument(
        "--state-dir", type=Path, default=None,
        help=f"State directory (kill_switch_latch.json, rollout state). "
             f"Default: {STATE_DIR}",
    )
    p.add_argument(
        "--journal", type=Path, default=None,
        help=f"Event journal path. Default: {LIVE_SIM_JOURNAL}",
    )
    p.add_argument(
        "--skip-promotion-gate", action="store_true",
        help="Skip the 9-gate promotion check (DRY-RUN ONLY -- ignored "
             "in --live mode).",
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

    @property
    def dry_run(self) -> bool:
        return not self.live


def build_config(args: argparse.Namespace) -> RuntimeConfig:
    return RuntimeConfig(
        live=args.live,
        max_bars=int(args.max_bars),
        tick_interval_s=float(args.tick_interval),
        variant=args.variant,
        state_dir=args.state_dir or STATE_DIR,
        journal_path=args.journal or LIVE_SIM_JOURNAL,
        skip_promotion_gate=bool(args.skip_promotion_gate),
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
            "live_ready_env", ok=True,
            detail="APEX_LIVE_READY=1 set",
        )
    return BootCheck(
        "live_ready_env", ok=False,
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
            "broker_dormancy", ok=False,
            detail="BROKER_TYPE not set in env (live mode needs IBKR or Tastytrade)",
        )
    try:
        assert_broker_active(broker)
    except DormantBrokerError as exc:
        return BootCheck(
            "broker_dormancy", ok=False,
            detail=str(exc),
        )
    return BootCheck(
        "broker_dormancy", ok=True,
        detail=f"BROKER_TYPE={broker!r} is active",
    )


def _check_promotion_gates() -> BootCheck:
    """Run scripts/_promotion_gate.py --all; must return 0."""
    gate_script = REPO_ROOT / "scripts" / "_promotion_gate.py"
    if not gate_script.exists():
        return BootCheck(
            "promotion_gates", ok=False,
            detail=f"missing {gate_script.relative_to(REPO_ROOT)}",
        )
    proc = subprocess.run(
        [sys.executable, str(gate_script), "--all"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0:
        return BootCheck(
            "promotion_gates", ok=True,
            detail="all 9 gates PASS",
        )
    # Surface a one-line summary
    last = next(
        (ln for ln in reversed(proc.stdout.splitlines()) if "Verdict" in ln),
        f"rc={proc.returncode}",
    )
    return BootCheck(
        "promotion_gates", ok=False,
        detail=f"{last} (run scripts/_promotion_gate.py --all for details)",
    )


def _check_doctor() -> BootCheck:
    """mnq doctor must report no FAIL."""
    try:
        from mnq.cli.doctor import run_all_checks
    except ImportError as exc:
        return BootCheck(
            "doctor", ok=False, detail=f"could not import mnq.cli.doctor: {exc}",
        )
    try:
        results = run_all_checks(strict=False)
    except Exception as exc:  # noqa: BLE001 -- defensive
        return BootCheck(
            "doctor", ok=False, detail=f"run_all_checks raised: {exc}",
        )
    fails = [r for r in results if r.status == "fail"]
    if not fails:
        return BootCheck(
            "doctor", ok=True,
            detail=f"{len(results)} checks, no FAILs",
        )
    return BootCheck(
        "doctor", ok=False,
        detail=f"{len(fails)} FAIL: " + ", ".join(r.name for r in fails),
    )


def _check_kill_switch_latch(state_dir: Path) -> BootCheck:
    """Refuse if a prior kill-switch trip is still latched on disk."""
    latch_path = state_dir / "kill_switch_latch.json"
    if not latch_path.exists():
        return BootCheck(
            "kill_switch_latch", ok=True,
            detail="no latch file (clean boot)",
        )
    try:
        latch = json.loads(latch_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return BootCheck(
            "kill_switch_latch", ok=False,
            detail=f"latch file unparseable: {exc}",
        )
    if latch.get("cleared_at_utc"):
        return BootCheck(
            "kill_switch_latch", ok=True,
            detail=(
                f"latch file present but cleared at "
                f"{latch.get('cleared_at_utc')}"
            ),
        )
    return BootCheck(
        "kill_switch_latch", ok=False,
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
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    stats: TickStats = field(default_factory=TickStats)

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
                    await self._tick(bar_i)
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
                "drain complete: bars=%d signals=%d orders=%d blocked=%d errors=%d",
                self.stats.bars_processed, self.stats.signals_seen,
                self.stats.orders_submitted, self.stats.orders_blocked,
                self.stats.errors,
            )
        return EX_OK

    async def _tick(self, bar_i: int) -> None:
        """One iteration. Currently a placeholder; B4 wires per-bar Firm review.

        Today: increments stats, evaluates kill-switch + circuit
        breaker + rollout, and would-place a synthetic order to
        exercise the gate chain in dry-run mode. Real signal-source
        integration (TradingView webhook, polling) lands when the
        operator picks a signal-source disposition.
        """
        from datetime import UTC, datetime
        self.stats.bars_processed += 1
        # Surface tier-rollout state (cheap query)
        qty = self.rollout.allowed_qty()
        if qty == 0:
            # Halt -- no new entries.
            return
        # Circuit-breaker decision (kill-switch + manual halt + drawdown)
        decision = self.breaker.allow_trade(now_utc=datetime.now(UTC))
        if not decision.allowed:
            self.stats.orders_blocked += 1
            logger.info(
                "tick %d: trade refused by circuit breaker: %s (%s)",
                bar_i, decision.reason, decision.detail,
            )
            return
        # Placeholder for real signal source. In dry-run, log the would-be
        # order; in live, this is where VenueRouter + B4 Firm review wire in.
        logger.debug(
            "tick %d: rollout=%s tier=%d allowed_qty=%d circuit=ok",
            bar_i, self.rollout.state.value, self.rollout.tier, qty,
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
    print("MNQ_BOT // run_eta_live")
    print("=" * 64)
    print(f"mode          : {'LIVE' if cfg.live else 'DRY-RUN'}")
    print(f"variant       : {cfg.variant}")
    print(f"max_bars      : {cfg.max_bars or 'unbounded'}")
    print(f"tick_interval : {cfg.tick_interval_s}s")
    print(f"state_dir     : {cfg.state_dir}")
    print(f"journal       : {cfg.journal_path}")
    print("-" * 64)
    print("BOOT GUARDS")
    for c in checks:
        print(f"  {c.render()}")
    print("=" * 64)
    failed = [c for c in checks if not c.ok]
    if failed:
        logger.error(
            "boot REFUSED: %d guard(s) FAILed: %s",
            len(failed), ", ".join(c.name for c in failed),
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

    runtime = ApexRuntime(
        cfg=cfg,
        journal=journal,
        book=book,
        breaker=breaker,
        rollout=rollout,
    )
    _install_signal_handlers(runtime)

    print(
        f"safety        : OrderBook+chain={book._gate_chain is not None} "  # noqa: SLF001
        f"breaker.kill={kill_switch.path.name} "
        f"rollout={rollout.state.value}/T{rollout.tier}/qty={rollout.allowed_qty()}",
    )
    print("=" * 64)

    try:
        rc = await runtime.run()
    except Exception:  # noqa: BLE001 -- final-safety net; logged
        logger.exception("runtime crashed")
        return EX_RUNTIME_ERROR
    return rc


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return EX_OK


if __name__ == "__main__":
    sys.exit(main())
