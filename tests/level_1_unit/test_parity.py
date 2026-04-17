"""Tests for parity dashboard."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from mnq.observability.parity import (
    compare_envs,
    render_report,
    summarize_env,
)
from mnq.storage.journal import EventJournal
from mnq.storage.schema import FILL_REALIZED


def create_test_journal(trades_data: list[dict]) -> Path:
    """Create a temporary journal with synthetic FILL_REALIZED events.

    Args:
        trades_data: List of dicts with trade parameters.

    Returns:
        Path to the temporary journal file.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite") as tmp:
        tmp_path = Path(tmp.name)

    journal = EventJournal(tmp_path, fsync=False)

    for trade in trades_data:
        payload = {
            "symbol": trade.get("symbol", "MNQ"),
            "side": trade.get("side", "long"),
            "entry_ts": trade["entry_ts"].isoformat(),
            "exit_ts": trade["exit_ts"].isoformat(),
            "entry_price": str(trade.get("entry_price", "18000.00")),
            "exit_price": str(trade.get("exit_price", "18010.00")),
            "qty": trade.get("qty", 1),
            "pnl_dollars": str(trade.get("pnl_dollars", "20.00")),
            "commission_dollars": str(trade.get("commission_dollars", "1.00")),
            "exit_reason": trade.get("exit_reason", "target_hit"),
            "slippage_ticks": trade.get("slippage_ticks", 0.5),
        }
        journal.append(FILL_REALIZED, payload, trace_id=trade.get("trace_id"))

    journal.close()
    return tmp_path


class TestSummarizeEnv:
    """Tests for summarize_env function."""

    def test_empty_journal(self) -> None:
        """Empty journal returns zero trades and zero PnL."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite") as tmp:
            tmp_path = Path(tmp.name)
        journal = EventJournal(tmp_path, fsync=False)

        summary = summarize_env(journal, env_label="test")

        assert summary.env == "test"
        assert summary.n_trades == 0
        assert summary.total_pnl == Decimal("0")
        assert summary.win_rate == 0.0
        assert summary.expectancy_dollars == Decimal("0")
        assert summary.avg_slippage_ticks == 0.0
        assert summary.n_rejected == 0

        journal.close()
        tmp_path.unlink()

    def test_five_trades(self) -> None:
        """Journal with 5 trades yields correct aggregates."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": str(20.0 * (i + 1)),  # 20, 40, 60, 80, 100
                "commission_dollars": "1.0",
                "trace_id": f"trade_{i}",
            }
            for i in range(5)
        ]
        journal_path = create_test_journal(trades_data)

        try:
            journal = EventJournal(journal_path, fsync=False)
            summary = summarize_env(journal, env_label="paper")

            assert summary.n_trades == 5
            assert summary.total_pnl == Decimal("300")  # 20 + 40 + 60 + 80 + 100
            assert summary.expectancy_dollars == Decimal("60")  # 300 / 5
            assert summary.win_rate == 1.0  # All trades are winners
            assert summary.avg_slippage_ticks == 0.5

            journal.close()
        finally:
            journal_path.unlink()

    def test_since_bound(self) -> None:
        """summarize_env respects since parameter."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "20.00",
                "commission_dollars": "1.0",
                "trace_id": f"trade_{i}",
            }
            for i in range(5)
        ]
        journal_path = create_test_journal(trades_data)

        try:
            journal = EventJournal(journal_path, fsync=False)
            # Filter to trades starting from hour 2 onwards
            since = base_time + timedelta(hours=2)
            summary = summarize_env(journal, env_label="paper", since=since)

            assert summary.n_trades == 3  # trades 2, 3, 4
            journal.close()
        finally:
            journal_path.unlink()

    def test_until_bound(self) -> None:
        """summarize_env respects until parameter."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "20.00",
                "commission_dollars": "1.0",
                "trace_id": f"trade_{i}",
            }
            for i in range(5)
        ]
        journal_path = create_test_journal(trades_data)

        try:
            journal = EventJournal(journal_path, fsync=False)
            # Filter to trades ending before hour 3
            until = base_time + timedelta(hours=3)
            summary = summarize_env(journal, env_label="paper", until=until)

            assert summary.n_trades == 3  # trades 0, 1, 2 (exit at 0:30, 1:30, 2:30)
            journal.close()
        finally:
            journal_path.unlink()

    def test_win_rate_excludes_scratches(self) -> None:
        """Win rate calculation excludes zero-PnL trades."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time,
                "exit_ts": base_time + timedelta(minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
            },
            {
                "entry_ts": base_time + timedelta(hours=1),
                "exit_ts": base_time + timedelta(hours=1, minutes=30),
                "pnl_dollars": "0.00",  # Scratch
                "commission_dollars": "0.0",
            },
            {
                "entry_ts": base_time + timedelta(hours=2),
                "exit_ts": base_time + timedelta(hours=2, minutes=30),
                "pnl_dollars": "-10.00",  # Loser
                "commission_dollars": "1.0",
            },
        ]
        journal_path = create_test_journal(trades_data)

        try:
            journal = EventJournal(journal_path, fsync=False)
            summary = summarize_env(journal, env_label="paper")

            # Win rate = 1 winner / (1 winner + 1 loser) = 50%
            assert summary.n_trades == 3
            assert summary.win_rate == 0.5

            journal.close()
        finally:
            journal_path.unlink()


class TestCompareEnvs:
    """Tests for compare_envs function."""

    def test_identical_envs_no_divergence(self) -> None:
        """Identical environments should not diverge."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "20.00",
                "commission_dollars": "1.0",
            }
            for i in range(3)
        ]

        paper_path = create_test_journal(trades_data)
        live_path = create_test_journal(trades_data)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")

            report = compare_envs(paper_summary, live_summary)

            assert not report.is_divergent
            assert len(report.alerts) == 0
            assert abs(report.expectancy_diff_dollars) < 0.01

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()

    def test_live_worse_pnl_triggers_alert(self) -> None:
        """Live environment systematically worse on PnL triggers alert."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)

        paper_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
            }
            for i in range(5)
        ]

        live_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "40.00",  # $10 worse per trade
                "commission_dollars": "1.0",
            }
            for i in range(5)
        ]

        paper_path = create_test_journal(paper_trades)
        live_path = create_test_journal(live_trades)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")

            report = compare_envs(
                paper_summary,
                live_summary,
                divergence_threshold_dollars=5.0,
            )

            assert report.is_divergent
            assert len(report.alerts) > 0
            assert "PnL divergence" in report.alerts[0]

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()

    def test_win_rate_divergence_triggers_alert(self) -> None:
        """Large win-rate delta triggers alert."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)

        paper_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00" if i < 4 else "-50.00",  # 80% win rate
                "commission_dollars": "1.0",
            }
            for i in range(5)
        ]

        live_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00" if i < 2 else "-50.00",  # 40% win rate
                "commission_dollars": "1.0",
            }
            for i in range(5)
        ]

        paper_path = create_test_journal(paper_trades)
        live_path = create_test_journal(live_trades)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")

            report = compare_envs(
                paper_summary,
                live_summary,
                divergence_threshold_wr=0.05,
            )

            assert report.is_divergent
            assert any("Win-rate" in alert for alert in report.alerts)

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()

    def test_slippage_divergence_triggers_alert(self) -> None:
        """Large slippage delta triggers alert."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)

        paper_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
                "slippage_ticks": 0.25,  # Low slippage
            }
            for i in range(5)
        ]

        live_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
                "slippage_ticks": 1.25,  # 1.0 tick higher
            }
            for i in range(5)
        ]

        paper_path = create_test_journal(paper_trades)
        live_path = create_test_journal(live_trades)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")

            report = compare_envs(
                paper_summary,
                live_summary,
                divergence_threshold_slip=0.5,
            )

            assert report.is_divergent
            assert any("Slippage" in alert for alert in report.alerts)

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()

    def test_below_threshold_no_alert(self) -> None:
        """Mismatches below threshold do not trigger alert."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)

        paper_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
                "slippage_ticks": 0.5,
            }
            for i in range(5)
        ]

        live_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "51.00",  # $1 per trade diff
                "commission_dollars": "1.0",
                "slippage_ticks": 0.7,  # 0.2 tick diff
            }
            for i in range(5)
        ]

        paper_path = create_test_journal(paper_trades)
        live_path = create_test_journal(live_trades)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")

            report = compare_envs(
                paper_summary,
                live_summary,
                divergence_threshold_dollars=5.0,
                divergence_threshold_slip=0.5,
            )

            assert not report.is_divergent
            assert len(report.alerts) == 0

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()


class TestRenderReport:
    """Tests for render_report function."""

    def test_render_produces_non_empty_string(self) -> None:
        """Rendered report should be a non-empty string with key fields."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time,
                "exit_ts": base_time + timedelta(minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
            }
        ]

        paper_path = create_test_journal(trades_data)
        live_path = create_test_journal(trades_data)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")
            report = compare_envs(paper_summary, live_summary)

            rendered = render_report(report)

            assert isinstance(rendered, str)
            assert len(rendered) > 0
            assert "PARITY REPORT" in rendered
            assert "PAPER TRADING" in rendered
            assert "LIVE TRADING" in rendered
            assert "OK ✓" in rendered or "DIVERGENT" in rendered

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()


class TestBootstrapCI:
    """Tests for paired bootstrap confidence interval."""

    def test_zero_diff_ci_contains_zero(self) -> None:
        """When PnL diffs are all exactly zero, CI should contain zero."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)

        # Both environments have identical PnL
        trades_data = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
            }
            for i in range(10)
        ]

        paper_path = create_test_journal(trades_data)
        live_path = create_test_journal(trades_data)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")
            report = compare_envs(paper_summary, live_summary)

            # CI should contain zero
            assert report.trade_pnl_diff_ci.lo <= 0.0
            assert report.trade_pnl_diff_ci.hi >= 0.0

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()

    def test_constant_diff_ci_excludes_zero(self) -> None:
        """When PnL diffs are consistently +$10, CI should exclude zero."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)

        paper_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
            }
            for i in range(10)
        ]

        live_trades = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "60.00",  # Consistently $10 better
                "commission_dollars": "1.0",
            }
            for i in range(10)
        ]

        paper_path = create_test_journal(paper_trades)
        live_path = create_test_journal(live_trades)

        try:
            paper_journal = EventJournal(paper_path, fsync=False)
            live_journal = EventJournal(live_path, fsync=False)

            paper_summary = summarize_env(paper_journal, env_label="paper")
            live_summary = summarize_env(live_journal, env_label="live")
            report = compare_envs(paper_summary, live_summary)

            # CI should exclude zero (all diffs are +$10)
            assert report.trade_pnl_diff_ci.lo > 0.0
            assert report.trade_pnl_diff_ci.hi > 0.0

            paper_journal.close()
            live_journal.close()
        finally:
            paper_path.unlink()
            live_path.unlink()
