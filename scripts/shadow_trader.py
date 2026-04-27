#!/usr/bin/env python3
"""Shadow trading driver — Phase 8 scaffold.

Runs the full Evolutionary Trading Algo pipeline against live market data WITHOUT
executing real trades. The shadow trader:

  1. Connects to NinjaTrader ATI for live MNQ quotes
  2. Feeds quotes through the Firm's 6-agent adversarial review
  3. Runs the gauntlet hard-gate with outcome-weighted scoring
  4. Routes "orders" through VenueRouter in shadow mode
  5. Runs the tolerance harness against paper vs shadow journals
  6. Logs everything for Phase 9 promotion decision

The shadow trader proves the entire pipeline works end-to-end
with real market data. No real orders ever hit the broker.

After 30+ days of clean shadow trading:
  - Tolerance harness must be GREEN
  - Kill switch audit must be CLEAR
  - Firm PM score must average >= 75
  - Edward (human) makes the final LIVE promotion call

Usage:
    python scripts/shadow_trader.py                    # live shadow
    python scripts/shadow_trader.py --replay FILE.csv  # replay historical
    python scripts/shadow_trader.py --status           # check state
    python scripts/shadow_trader.py --days 30          # set sim gate

Configuration via environment:
    NT_HOST, NT_PORT, NT_ACCOUNT   — NinjaTrader ATI
    FIRM_CODE_PATH                 — The Firm package
    SHADOW_JOURNAL_PATH            — Shadow journal location
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mnq.executor.orders import OrderBook  # noqa: E402
from mnq.executor.venue_router import VenueRouter  # noqa: E402
from mnq.risk.heat_budget import CanonicalRegime, HeatBudget  # noqa: E402
from mnq.storage.journal import EventJournal  # noqa: E402
from mnq.venues.ninjatrader import NinjaTraderVenue, NTConfig  # noqa: E402

logger = logging.getLogger("shadow_trader")

# Paths
DATA_ROOT = REPO_ROOT / "data"
SHADOW_JOURNAL_PATH = DATA_ROOT / "shadow" / "journal.sqlite"
SHADOW_STATE_PATH = DATA_ROOT / "shadow" / "state.json"
SHADOW_REPORT_PATH = REPO_ROOT / "reports" / "shadow_trading.md"

# Shadow trading config
DEFAULT_SYMBOL = "MNQ"
DEFAULT_SIM_GATE_DAYS = 30
EVALUATION_INTERVAL_TRADES = 5  # Run tolerance check every N trades


class ShadowState:
    """Persistent shadow trading state."""

    def __init__(self, path: Path = SHADOW_STATE_PATH):
        self.path = path
        self.start_date: str | None = None
        self.current_day: int = 0
        self.target_days: int = DEFAULT_SIM_GATE_DAYS
        self.total_signals: int = 0
        self.total_trades: int = 0
        self.total_blocked: int = 0
        self.firm_verdicts: list[dict] = []  # last 50
        self.avg_pm_score: float = 0.0
        self.tolerance_status: str = "OK"
        self.kill_switch_clear: bool = True
        self.promotion_eligible: bool = False
        self.sessions: list[dict] = []  # last 30 sessions
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for k, v in data.items():
                    if hasattr(self, k):
                        setattr(self, k, v)
            except (json.JSONDecodeError, KeyError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "start_date": self.start_date,
                    "current_day": self.current_day,
                    "target_days": self.target_days,
                    "total_signals": self.total_signals,
                    "total_trades": self.total_trades,
                    "total_blocked": self.total_blocked,
                    "firm_verdicts": self.firm_verdicts[-50:],
                    "avg_pm_score": self.avg_pm_score,
                    "tolerance_status": self.tolerance_status,
                    "kill_switch_clear": self.kill_switch_clear,
                    "promotion_eligible": self.promotion_eligible,
                    "sessions": self.sessions[-30:],
                },
                indent=2,
                default=str,
            )
        )

    def check_promotion(self) -> tuple[bool, list[str]]:
        """Check if shadow trading meets promotion criteria."""
        reasons: list[str] = []

        if self.current_day < self.target_days:
            reasons.append(
                f"Need {self.target_days - self.current_day} more days "
                f"({self.current_day}/{self.target_days})"
            )

        if self.avg_pm_score < 75.0:
            reasons.append(f"Avg PM score {self.avg_pm_score:.1f} < 75.0")

        if self.tolerance_status != "OK":
            reasons.append(f"Tolerance status: {self.tolerance_status}")

        if not self.kill_switch_clear:
            reasons.append("Kill switch not clear")

        if self.total_trades < 20:
            reasons.append(f"Only {self.total_trades} trades (need >= 20)")

        eligible = len(reasons) == 0
        self.promotion_eligible = eligible
        return eligible, reasons


class ShadowTrader:
    """Orchestrates shadow trading sessions."""

    def __init__(
        self,
        state: ShadowState | None = None,
        nt_config: NTConfig | None = None,
    ):
        self.state = state or ShadowState()
        self.nt_config = nt_config or NTConfig.from_env()
        self.nt_config.venue_type = "paper"  # Always paper in shadow
        self._running = False

    async def run_session(self) -> dict:
        """Run one shadow trading session.

        A session connects to NinjaTrader, listens for signals,
        runs them through the Firm pipeline, and logs everything.
        """
        session_start = datetime.now(tz=UTC)
        session_result = {
            "start": session_start.isoformat(),
            "end": None,
            "signals": 0,
            "trades": 0,
            "blocked": 0,
            "errors": [],
        }

        # Initialize components
        # B3 closure (v0.2.3): shadow mode mirrors live; use the full
        # 5-gate chain so the shadow run sees the same gates production
        # would. Operator can set MNQ_SHADOW_NO_GATES=1 to bypass for
        # debugging (then explicitly use the unsafe factory).
        from mnq.risk.gate_chain import build_default_chain

        journal = EventJournal(SHADOW_JOURNAL_PATH)
        if os.environ.get("MNQ_SHADOW_NO_GATES") == "1":
            order_book = OrderBook.unsafe_no_gate_chain(journal)
        else:
            order_book = OrderBook(journal, build_default_chain())
        venue = NinjaTraderVenue(self.nt_config)
        heat_budget = HeatBudget(regime=CanonicalRegime.TRANSITION)  # noqa: F841 -- reserved for v0.2.x heat-budget gate

        try:
            await venue.connect()
            logger.info("Connected to NinjaTrader ATI — shadow mode")
        except ConnectionError as e:
            logger.error("Cannot connect to NinjaTrader: %s", e)
            session_result["errors"].append(str(e))
            return session_result

        router = VenueRouter(order_book, venue, shadow=True)
        self._running = True

        try:
            # Subscribe to MNQ quotes
            logger.info("Subscribing to %s quotes...", DEFAULT_SYMBOL)

            # Main loop: process quotes and generate signals
            # In a real implementation, this processes tick data through
            # the Apex V3 engine to generate entry signals, then runs
            # them through the Firm review pipeline.
            #
            # For now, this is the scaffold — the actual signal generation
            # will be wired when Apex V3 adapter is connected.

            while self._running:
                # Check venue health
                if not await venue.heartbeat():
                    logger.warning("Venue heartbeat failed, reconnecting...")
                    await venue.disconnect()
                    await asyncio.sleep(5)
                    try:
                        await venue.connect()
                    except ConnectionError:
                        logger.error("Reconnect failed")
                        break

                # Placeholder: In production, this loop processes incoming
                # ticks, generates signals via Apex V3, and runs them
                # through the Firm. For now, sleep and monitor.
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Session cancelled")
        except Exception as e:
            logger.exception("Session error: %s", e)
            session_result["errors"].append(str(e))
        finally:
            self._running = False
            await venue.disconnect()
            journal.close()

        session_result["end"] = datetime.now(tz=UTC).isoformat()
        session_result["trades"] = router.stats.orders_routed
        session_result["blocked"] = router.stats.orders_blocked

        # Update state
        self.state.sessions.append(session_result)
        if not self.state.start_date:
            self.state.start_date = session_start.date().isoformat()
        self.state.total_trades += session_result["trades"]
        self.state.total_blocked += session_result["blocked"]
        self.state.save()

        return session_result

    def stop(self) -> None:
        """Signal the session to stop gracefully."""
        self._running = False


def render_status(state: ShadowState) -> str:
    """Render shadow trading status as markdown."""
    eligible, reasons = state.check_promotion()
    lines = [
        f"# Shadow Trading Status — {datetime.now(tz=UTC).isoformat()[:19]}Z",
        "",
        f"**Day:** {state.current_day} / {state.target_days}",
        f"**Total signals:** {state.total_signals}",
        f"**Total trades:** {state.total_trades}",
        f"**Total blocked:** {state.total_blocked}",
        f"**Avg PM score:** {state.avg_pm_score:.1f}",
        f"**Tolerance:** {state.tolerance_status}",
        f"**Kill switch:** {'CLEAR' if state.kill_switch_clear else 'TRIPPED'}",
        "",
        f"## Promotion Eligibility: {'ELIGIBLE' if eligible else 'NOT YET'}",
        "",
    ]
    if reasons:
        lines.append("**Blockers:**")
        for r in reasons:
            lines.append(f"- {r}")
    else:
        lines.append("All gates GREEN. **Human promotion call required.**")
        lines.append("")
        lines.append("To promote to LIVE:")
        lines.append("1. Review the shadow trading report")
        lines.append("2. Run `python scripts/shadow_trader.py --promote`")
        lines.append("3. Edward Avila must manually confirm")

    return "\n".join(lines) + "\n"


async def async_main(args: argparse.Namespace) -> int:
    """Async entry point."""
    state = ShadowState()

    if args.status:
        print(render_status(state))
        eligible, _ = state.check_promotion()
        return 0 if eligible else 1

    if args.days:
        state.target_days = args.days
        state.save()
        print(f"Shadow sim gate set to {args.days} days")
        return 0

    # Run shadow trading session
    state.target_days = args.days or state.target_days
    trader = ShadowTrader(state)

    # Handle Ctrl+C gracefully
    loop = asyncio.get_event_loop()

    def _stop():
        logger.info("Shutdown signal received")
        trader.stop()

    import contextlib

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows doesn't support add_signal_handler -- skip cleanly.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stop)

    logger.info(
        "Starting shadow trading session (day %d/%d)",
        state.current_day,
        state.target_days,
    )
    result = await trader.run_session()

    # Write report
    SHADOW_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHADOW_REPORT_PATH.write_text(render_status(state))

    logger.info(
        "Session complete: trades=%d blocked=%d errors=%d",
        result["trades"],
        result["blocked"],
        len(result["errors"]),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shadow trading driver.")
    parser.add_argument("--status", action="store_true", help="Print status.")
    parser.add_argument("--days", type=int, default=None, help="Set sim gate target days.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
