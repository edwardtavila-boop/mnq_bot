"""Unit tests for scripts/backtest_baseline_export.py.

Exercises the fill loader and report renderer against a synthetic
SQLite journal. Keeps the test isolated from live_sim's real journal.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "backtest_baseline_export.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("backtest_baseline_export", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backtest_baseline_export"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bb_mod():
    return _load_mod()


@pytest.fixture
def synth_journal(tmp_path):
    db = tmp_path / "journal.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event_type TEXT NOT NULL,
            trace_id TEXT,
            payload TEXT
        );
    """)
    t0 = datetime(2026, 4, 16, 14, 30, tzinfo=UTC)
    entries = [
        (t0,                      "order.filled", "t1", {"side": "long",  "fill_qty": 1, "fill_price": 21000.0}),
        (t0 + timedelta(minutes=5),  "order.filled", "t1", {"side": "long",  "fill_qty": 1, "fill_price": 21010.0}),
        (t0 + timedelta(minutes=10), "order.filled", "t2", {"side": "short", "fill_qty": 1, "fill_price": 21008.0}),
        (t0 + timedelta(minutes=15), "order.filled", "t2", {"side": "short", "fill_qty": 1, "fill_price": 21002.0}),
    ]
    for ts, et, tid, p in entries:
        conn.execute(
            "INSERT INTO events (ts, event_type, trace_id, payload) VALUES (?,?,?,?)",
            (ts.isoformat(), et, tid, json.dumps(p)),
        )
    conn.commit()
    conn.close()
    return db


class TestLoadLiveTrades:
    def test_pairs_consecutive_fills_into_trades(self, bb_mod, synth_journal):
        trades = bb_mod._load_live_trades(synth_journal)
        assert len(trades) == 2

    def test_empty_db_returns_empty_list(self, bb_mod, tmp_path):
        missing = tmp_path / "nope.sqlite"
        assert bb_mod._load_live_trades(missing) == []

    def test_long_trade_pnl_positive_when_price_rises(self, bb_mod, synth_journal):
        trades = bb_mod._load_live_trades(synth_journal)
        long_trade = trades[0]
        assert long_trade.side == "long"
        # (21010 - 21000) * +1 * 1 * 2.0 = +20
        assert long_trade.pnl == pytest.approx(20.0)

    def test_short_trade_pnl_positive_when_price_falls(self, bb_mod, synth_journal):
        trades = bb_mod._load_live_trades(synth_journal)
        short_trade = trades[1]
        assert short_trade.side == "short"
        # (21002 - 21008) * -1 * 1 * 2.0 = +12
        assert short_trade.pnl == pytest.approx(12.0)

    def test_trade_seq_is_one_indexed(self, bb_mod, synth_journal):
        trades = bb_mod._load_live_trades(synth_journal)
        assert trades[0].seq == 1
        assert trades[1].seq == 2


class TestReportRender:
    def test_report_has_parity_table_columns(self, bb_mod, synth_journal):
        trades = bb_mod._load_live_trades(synth_journal)
        report = bb_mod._render_pnl_report(trades)
        # Parity harness parses this schema — lock it in.
        assert "| # | entry_ts | side | qty | entry_px | exit_ts | exit_px | pnl |" in report

    def test_report_contains_each_trade_row(self, bb_mod, synth_journal):
        trades = bb_mod._load_live_trades(synth_journal)
        report = bb_mod._render_pnl_report(trades)
        for t in trades:
            assert f"{t.entry_px:.2f}" in report
            assert f"{t.exit_px:.2f}" in report

    def test_net_pnl_line_present(self, bb_mod, synth_journal):
        trades = bb_mod._load_live_trades(synth_journal)
        report = bb_mod._render_pnl_report(trades)
        assert "Net PnL" in report


class TestJsonlExport:
    def test_writes_one_line_per_trade(self, bb_mod, synth_journal, tmp_path):
        trades = bb_mod._load_live_trades(synth_journal)
        out = tmp_path / "fills.jsonl"
        bb_mod._write_jsonl(trades, out)
        lines = out.read_text().strip().splitlines()
        assert len(lines) == len(trades)
        for line in lines:
            rec = json.loads(line)
            assert {"seq", "entry_ts", "exit_ts", "side", "qty",
                    "entry_px", "exit_px", "pnl"}.issubset(rec.keys())
