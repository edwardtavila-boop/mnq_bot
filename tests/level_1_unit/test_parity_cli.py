"""Tests for parity CLI subcommand."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from mnq.cli.main import app
from mnq.storage.journal import EventJournal
from mnq.storage.schema import FILL_REALIZED

runner = CliRunner()


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


class TestParityCLI:
    """Tests for mnq parity CLI subcommand."""

    def test_compare_identical_journals_exits_zero(self) -> None:
        """Comparing identical journals should exit with code 0."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
            }
            for i in range(3)
        ]

        paper_path = create_test_journal(trades_data)
        live_path = create_test_journal(trades_data)

        try:
            result = runner.invoke(
                app,
                [
                    "parity",
                    "compare",
                    "--paper", str(paper_path),
                    "--live", str(live_path),
                ],
            )
            assert result.exit_code == 0
            assert "OK" in result.output or "ok" in result.output.lower()
        finally:
            paper_path.unlink()
            live_path.unlink()

    def test_compare_diverged_journals_exits_two(self) -> None:
        """Comparing diverged journals should exit with code 2."""
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
                "pnl_dollars": "30.00",  # $20 worse per trade (exceeds threshold)
                "commission_dollars": "1.0",
            }
            for i in range(5)
        ]

        paper_path = create_test_journal(paper_trades)
        live_path = create_test_journal(live_trades)

        try:
            result = runner.invoke(
                app,
                [
                    "parity",
                    "compare",
                    "--paper", str(paper_path),
                    "--live", str(live_path),
                    "--threshold-pnl", "5.0",
                ],
            )
            assert result.exit_code == 2
            assert "DIVERGENT" in result.output or "divergent" in result.output.lower()
        finally:
            paper_path.unlink()
            live_path.unlink()

    def test_compare_json_output_is_valid(self) -> None:
        """--json flag should produce valid JSON output."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        trades_data = [
            {
                "entry_ts": base_time + timedelta(hours=i),
                "exit_ts": base_time + timedelta(hours=i, minutes=30),
                "pnl_dollars": "50.00",
                "commission_dollars": "1.0",
            }
            for i in range(3)
        ]

        paper_path = create_test_journal(trades_data)
        live_path = create_test_journal(trades_data)

        try:
            result = runner.invoke(
                app,
                [
                    "parity",
                    "compare",
                    "--paper", str(paper_path),
                    "--live", str(live_path),
                    "--json",
                ],
            )
            assert result.exit_code in (0, 2)
            # Should be valid JSON
            payload = json.loads(result.output)
            assert "paper" in payload
            assert "live" in payload
            assert "comparison" in payload
            assert "alerts" in payload
            assert "is_divergent" in payload
        finally:
            paper_path.unlink()
            live_path.unlink()
