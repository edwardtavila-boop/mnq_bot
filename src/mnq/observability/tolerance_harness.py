"""Automated tolerance harness — Phase 4 completion.

Wraps the parity engine (observability/parity.py) into an automated
divergence monitor with:
  - Configurable per-metric thresholds (PnL, win-rate, slippage, fill-rate)
  - Rolling-window evaluation (default: last 50 trades)
  - Alert severity levels (INFO → WARNING → CRITICAL → HALT)
  - Gate chain integration (emits a pre_trade_gate.json HOT when HALT)
  - Structured JSON output for dashboard consumption
  - Persistent state tracking (consecutive breach counter)

Integration:
  - Called by live_sim.py after every N trades (default: 5)
  - Reads paper journal + live/shadow journal
  - Writes tolerance_state.json to data/
  - On HALT: writes pre_trade_gate.json HOT → gate_chain blocks new trades

Usage:
    harness = ToleranceHarness(config)
    result = harness.evaluate(paper_journal, live_journal)
    if result.should_halt:
        # gate chain will auto-block via pre_trade_gate.json
        pass
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import numpy as np

from mnq.observability.parity import (
    ParityReport,
    compare_envs,
    summarize_env,
)
from mnq.storage.journal import EventJournal


class AlertSeverity(str, Enum):
    """Escalation levels for tolerance breaches."""

    OK = "OK"
    INFO = "INFO"  # Within tolerance but worth noting
    WARNING = "WARNING"  # Approaching threshold
    CRITICAL = "CRITICAL"  # Threshold breached
    HALT = "HALT"  # Trading must stop


@dataclass(frozen=True)
class ToleranceThresholds:
    """Per-metric tolerance thresholds.

    Two tiers: warning (yellow) and critical (red).
    HALT triggers after consecutive_critical_max consecutive CRITICAL evals.
    """

    # Per-trade PnL divergence (dollars)
    pnl_warning: float = 3.0
    pnl_critical: float = 6.0

    # Win-rate delta (absolute, e.g. 0.05 = 5%)
    wr_warning: float = 0.04
    wr_critical: float = 0.08

    # Slippage delta (ticks)
    slip_warning: float = 0.3
    slip_critical: float = 0.75

    # Fill-rate delta (fraction of orders that actually fill)
    fill_rate_warning: float = 0.05
    fill_rate_critical: float = 0.10

    # Consecutive CRITICAL evaluations before HALT
    consecutive_critical_max: int = 3

    # Minimum trades required for evaluation (below this → INFO only)
    min_trades: int = 10

    # Rolling window size (last N trades)
    rolling_window: int = 50

    # Bootstrap resamples
    n_boot: int = 1000


@dataclass
class ToleranceAlert:
    """Single metric's tolerance result."""

    metric: str
    value: float
    threshold_warning: float
    threshold_critical: float
    severity: AlertSeverity
    message: str


@dataclass
class ToleranceResult:
    """Full tolerance evaluation result."""

    timestamp: str
    overall_severity: AlertSeverity
    alerts: list[ToleranceAlert]
    parity_report: ParityReport | None
    n_paper_trades: int
    n_live_trades: int
    consecutive_critical: int
    should_halt: bool
    halt_reason: str | None = None

    def as_dict(self) -> dict:
        """Serialize for JSON output (excludes ParityReport internals)."""
        return {
            "timestamp": self.timestamp,
            "overall_severity": self.overall_severity.value,
            "alerts": [
                {
                    "metric": a.metric,
                    "value": round(a.value, 4),
                    "threshold_warning": a.threshold_warning,
                    "threshold_critical": a.threshold_critical,
                    "severity": a.severity.value,
                    "message": a.message,
                }
                for a in self.alerts
            ],
            "n_paper_trades": self.n_paper_trades,
            "n_live_trades": self.n_live_trades,
            "consecutive_critical": self.consecutive_critical,
            "should_halt": self.should_halt,
            "halt_reason": self.halt_reason,
        }


@dataclass
class HarnessState:
    """Persistent state between evaluations."""

    consecutive_critical: int = 0
    total_evaluations: int = 0
    total_halts: int = 0
    last_evaluation: str | None = None
    last_severity: str = "OK"
    history: list[dict] = field(default_factory=list)  # last 20 evals

    @classmethod
    def load(cls, path: Path) -> HarnessState:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(
                consecutive_critical=data.get("consecutive_critical", 0),
                total_evaluations=data.get("total_evaluations", 0),
                total_halts=data.get("total_halts", 0),
                last_evaluation=data.get("last_evaluation"),
                last_severity=data.get("last_severity", "OK"),
                history=data.get("history", []),
            )
        except (json.JSONDecodeError, KeyError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "consecutive_critical": self.consecutive_critical,
                    "total_evaluations": self.total_evaluations,
                    "total_halts": self.total_halts,
                    "last_evaluation": self.last_evaluation,
                    "last_severity": self.last_severity,
                    "history": self.history[-20:],  # keep last 20
                },
                indent=2,
            )
        )


class ToleranceHarness:
    """Automated tolerance monitor wrapping the parity engine."""

    def __init__(
        self,
        thresholds: ToleranceThresholds | None = None,
        state_path: Path | None = None,
        gate_path: Path | None = None,
    ):
        self.thresholds = thresholds or ToleranceThresholds()
        repo_root = Path(__file__).resolve().parents[3]
        self.state_path = state_path or (repo_root / "data" / "tolerance_state.json")
        self.gate_path = gate_path or (repo_root / "data" / "pre_trade_gate.json")
        self.state = HarnessState.load(self.state_path)

    def _classify(
        self, metric: str, value: float, warn: float, crit: float
    ) -> ToleranceAlert:
        """Classify a single metric against warning/critical thresholds."""
        abs_val = abs(value)
        if abs_val >= crit:
            severity = AlertSeverity.CRITICAL
            msg = f"{metric}: {value:+.4f} exceeds critical threshold ({crit})"
        elif abs_val >= warn:
            severity = AlertSeverity.WARNING
            msg = f"{metric}: {value:+.4f} exceeds warning threshold ({warn})"
        elif abs_val > 0:
            severity = AlertSeverity.INFO
            msg = f"{metric}: {value:+.4f} within tolerance"
        else:
            severity = AlertSeverity.OK
            msg = f"{metric}: no divergence"

        return ToleranceAlert(
            metric=metric,
            value=value,
            threshold_warning=warn,
            threshold_critical=crit,
            severity=severity,
            message=msg,
        )

    def _emit_halt(self, reason: str) -> None:
        """Write pre_trade_gate.json HOT to block trading via gate chain."""
        self.gate_path.parent.mkdir(parents=True, exist_ok=True)
        self.gate_path.write_text(
            json.dumps(
                {
                    "state": "HOT",
                    "reason": f"tolerance_harness: {reason}",
                    "ts": datetime.now(tz=UTC).isoformat(),
                    "source": "tolerance_harness",
                }
            )
        )

    def evaluate(
        self,
        paper_journal: EventJournal,
        live_journal: EventJournal,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> ToleranceResult:
        """Run a tolerance evaluation against paper and live journals.

        Args:
            paper_journal: Journal for paper/sim trades.
            live_journal: Journal for live/shadow trades.
            since: Only consider trades after this time.
            until: Only consider trades before this time.

        Returns:
            ToleranceResult with severity, alerts, and halt decision.
        """
        now = datetime.now(tz=UTC)
        th = self.thresholds

        # Summarize both environments
        paper_summary = summarize_env(
            paper_journal, env_label="paper", since=since, until=until
        )
        live_summary = summarize_env(
            live_journal, env_label="live", since=since, until=until
        )

        n_paper = paper_summary.n_trades
        n_live = live_summary.n_trades

        # Not enough data → INFO-only result
        if min(n_paper, n_live) < th.min_trades:
            result = ToleranceResult(
                timestamp=now.isoformat(),
                overall_severity=AlertSeverity.INFO,
                alerts=[],
                parity_report=None,
                n_paper_trades=n_paper,
                n_live_trades=n_live,
                consecutive_critical=self.state.consecutive_critical,
                should_halt=False,
                halt_reason=None,
            )
            self._update_state(result)
            return result

        # Run full parity comparison
        report = compare_envs(
            paper_summary,
            live_summary,
            divergence_threshold_dollars=th.pnl_critical,
            divergence_threshold_wr=th.wr_critical,
            divergence_threshold_slip=th.slip_critical,
            n_boot=th.n_boot,
        )

        # Classify each metric
        alerts: list[ToleranceAlert] = []

        # PnL divergence
        pnl_diff = report.trade_pnl_diff_ci.point
        alerts.append(
            self._classify("pnl_per_trade", pnl_diff, th.pnl_warning, th.pnl_critical)
        )

        # Win-rate divergence
        alerts.append(
            self._classify(
                "win_rate", report.win_rate_diff, th.wr_warning, th.wr_critical
            )
        )

        # Slippage divergence
        alerts.append(
            self._classify(
                "slippage_ticks",
                report.slippage_diff_ticks,
                th.slip_warning,
                th.slip_critical,
            )
        )

        # Fill-rate divergence (computed from rejected counts)
        if n_paper > 0 and n_live > 0:
            paper_fill = n_paper / (n_paper + paper_summary.n_rejected) if (n_paper + paper_summary.n_rejected) > 0 else 1.0
            live_fill = n_live / (n_live + live_summary.n_rejected) if (n_live + live_summary.n_rejected) > 0 else 1.0
            fill_diff = live_fill - paper_fill
            alerts.append(
                self._classify(
                    "fill_rate",
                    fill_diff,
                    th.fill_rate_warning,
                    th.fill_rate_critical,
                )
            )

        # Determine overall severity
        severities = [a.severity for a in alerts]
        if AlertSeverity.CRITICAL in severities:
            overall = AlertSeverity.CRITICAL
            self.state.consecutive_critical += 1
        elif AlertSeverity.WARNING in severities:
            overall = AlertSeverity.WARNING
            self.state.consecutive_critical = 0
        else:
            overall = AlertSeverity.OK
            self.state.consecutive_critical = 0

        # HALT check
        should_halt = self.state.consecutive_critical >= th.consecutive_critical_max
        halt_reason = None
        if should_halt:
            halt_reason = (
                f"{self.state.consecutive_critical} consecutive CRITICAL evaluations "
                f"(threshold: {th.consecutive_critical_max})"
            )
            overall = AlertSeverity.HALT
            self._emit_halt(halt_reason)
            self.state.total_halts += 1

        result = ToleranceResult(
            timestamp=now.isoformat(),
            overall_severity=overall,
            alerts=alerts,
            parity_report=report,
            n_paper_trades=n_paper,
            n_live_trades=n_live,
            consecutive_critical=self.state.consecutive_critical,
            should_halt=should_halt,
            halt_reason=halt_reason,
        )

        self._update_state(result)
        return result

    def _update_state(self, result: ToleranceResult) -> None:
        """Persist harness state after evaluation."""
        self.state.total_evaluations += 1
        self.state.last_evaluation = result.timestamp
        self.state.last_severity = result.overall_severity.value
        self.state.history.append(
            {
                "ts": result.timestamp,
                "severity": result.overall_severity.value,
                "n_alerts": len(result.alerts),
                "consecutive_critical": result.consecutive_critical,
                "halt": result.should_halt,
            }
        )
        self.state.save(self.state_path)

    def reset_halt(self) -> None:
        """Manual reset after operator reviews the halt condition.

        Clears the pre_trade_gate.json and resets consecutive critical count.
        """
        self.state.consecutive_critical = 0
        self.state.save(self.state_path)
        if self.gate_path.exists():
            self.gate_path.write_text(
                json.dumps(
                    {
                        "state": "COLD",
                        "reason": "tolerance_harness: manual reset",
                        "ts": datetime.now(tz=UTC).isoformat(),
                        "source": "tolerance_harness",
                    }
                )
            )
