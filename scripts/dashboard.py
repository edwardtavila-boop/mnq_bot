"""Streamlit dashboard for the live-sim journal.

Reads the SQLite event journal at
``/sessions/kind-keen-faraday/data/live_sim/journal.sqlite`` (default,
overridable via the sidebar) and renders:

* Pipeline counters (signals → orders → fills → closures, blocks, halts)
* Per-regime PnL & win-rate
* Slippage distribution (overall and per regime)
* Daily PnL ladder + cumulative equity curve
* Drift status (turnover z-score history)
* Per-trade table with filters

Run with::

    streamlit run scripts/dashboard.py

(Streamlit isn't a hard dep — the script also exits cleanly with a
helpful message if Streamlit isn't installed.)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import streamlit as st  # type: ignore[import-untyped]
except ImportError:
    print(
        "ERROR: streamlit is not installed. Install with `pip install streamlit` "
        "or run scripts/dashboard_static.py for a non-interactive HTML dump.",
        file=sys.stderr,
    )
    sys.exit(2)

import pandas as pd  # noqa: E402

from mnq.storage.journal import EventJournal  # noqa: E402
from mnq.storage.schema import (  # noqa: E402
    DRIFT_ALERT,
    DRIFT_OK,
    FILL_REALIZED,
    ORDER_FILLED,
    ORDER_SUBMITTED,
    POSITION_UPDATE,
)

DEFAULT_JOURNAL = "/sessions/kind-keen-faraday/data/live_sim/journal.sqlite"

st.set_page_config(page_title="EVOLUTIONARY TRADING ALGO // Paper Sim Dashboard", layout="wide")

st.title("🎯 EVOLUTIONARY TRADING ALGO // Paper Sim Dashboard")

# ---------------------------------------------------------------------------
# Sidebar — journal selection
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Journal")
    journal_path = st.text_input("Path", value=DEFAULT_JOURNAL)
    refresh = st.button("Refresh")
    st.caption(
        "Reads the event SQLite journal in WAL mode. The same store the bot writes to."
    )

if not Path(journal_path).exists():
    st.error(f"Journal not found: {journal_path}")
    st.stop()

journal = EventJournal(Path(journal_path))


# ---------------------------------------------------------------------------
# Loaders (cached)
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def load_counters(path: str) -> dict[str, int]:
    j = EventJournal(Path(path))
    counters: dict[str, int] = defaultdict(int)
    for entry in j.replay():
        counters[entry.event_type] += 1
    return dict(counters)


@st.cache_data(show_spinner=False)
def load_trades(path: str) -> pd.DataFrame:
    j = EventJournal(Path(path))
    rows = []
    for entry in j.replay(event_types=(FILL_REALIZED,)):
        p = entry.payload
        if "pnl_dollars" not in p or "entry_ts" not in p:
            continue
        try:
            rows.append({
                "trace_id": entry.trace_id,
                "entry_ts": p.get("entry_ts"),
                "exit_ts": p.get("exit_ts"),
                "side": p.get("side"),
                "qty": p.get("qty"),
                "entry_price": float(p.get("entry_price", 0)),
                "exit_price": float(p.get("exit_price", 0)),
                "pnl_dollars": float(p.get("pnl_dollars", 0)),
                "commission_dollars": float(p.get("commission_dollars", 0)),
                "exit_reason": p.get("exit_reason"),
                "regime": p.get("regime", "unknown"),
                "slippage_ticks": float(p.get("slippage_ticks", 0)),
                "entry_slip_ticks": float(p.get("entry_slip_ticks", 0)),
            })
        except (ValueError, TypeError):
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["entry_dt"] = pd.to_datetime(df["entry_ts"], errors="coerce")
        df["exit_dt"] = pd.to_datetime(df["exit_ts"], errors="coerce")
        df["date"] = df["entry_dt"].dt.date
    return df


@st.cache_data(show_spinner=False)
def load_drift(path: str) -> pd.DataFrame:
    j = EventJournal(Path(path))
    rows = []
    for entry in j.replay(event_types=(DRIFT_OK, DRIFT_ALERT)):
        p = entry.payload
        rows.append({
            "ts": entry.ts,
            "event": entry.event_type,
            "metric": p.get("metric"),
            "z_score": float(p.get("z_score", 0)),
            "realized": float(p.get("realized", 0)),
        })
    return pd.DataFrame(rows)


# Force-clear cache when refresh button hit
if refresh:
    load_counters.clear()
    load_trades.clear()
    load_drift.clear()


# ---------------------------------------------------------------------------
# Top: pipeline counters
# ---------------------------------------------------------------------------

counters = load_counters(journal_path)
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Signals", counters.get(ORDER_SUBMITTED, 0))
col2.metric("Fills", counters.get(ORDER_FILLED, 0))
col3.metric("Round trips", counters.get(FILL_REALIZED, 0))
col4.metric("Position updates", counters.get(POSITION_UPDATE, 0))
col5.metric("Drift events",
            counters.get(DRIFT_OK, 0) + counters.get(DRIFT_ALERT, 0))

# ---------------------------------------------------------------------------
# Trades panel
# ---------------------------------------------------------------------------

trades = load_trades(journal_path)
if trades.empty:
    st.warning("No closed trades in journal yet.")
    st.stop()

total_pnl = trades["pnl_dollars"].sum()
n_trades = len(trades)
wr = (trades["pnl_dollars"] > 0).mean()
exp_per_trade = total_pnl / n_trades if n_trades else 0.0
mean_slip = trades["slippage_ticks"].mean()

st.subheader("Aggregate")
ca, cb, cc, cd = st.columns(4)
ca.metric("Net PnL", f"${total_pnl:,.2f}")
cb.metric("Trades", n_trades)
cc.metric("Win rate", f"{wr:.1%}")
cd.metric("Avg slippage", f"{mean_slip:+.2f} ticks")

# Per-regime
st.subheader("Per regime")
by_regime = (
    trades.groupby("regime")
    .agg(n=("pnl_dollars", "size"),
         wins=("pnl_dollars", lambda s: (s > 0).sum()),
         pnl=("pnl_dollars", "sum"),
         avg_slip=("slippage_ticks", "mean"))
    .reset_index()
)
by_regime["win_rate"] = by_regime["wins"] / by_regime["n"]
st.dataframe(by_regime, use_container_width=True)

# Per exit reason
st.subheader("Per exit reason")
by_reason = (
    trades.groupby("exit_reason")
    .agg(n=("pnl_dollars", "size"), pnl=("pnl_dollars", "sum"))
    .reset_index()
)
st.dataframe(by_reason, use_container_width=True)

# Daily PnL + equity
st.subheader("Daily PnL & equity curve")
daily = (
    trades.groupby("date")
    .agg(pnl=("pnl_dollars", "sum"), n=("pnl_dollars", "size"))
    .reset_index()
    .sort_values("date")
)
daily["equity"] = daily["pnl"].cumsum()
left, right = st.columns(2)
with left:
    st.bar_chart(daily.set_index("date")["pnl"])
with right:
    st.line_chart(daily.set_index("date")["equity"])

# Slippage histogram
st.subheader("Slippage distribution (ticks)")
st.bar_chart(trades["slippage_ticks"].value_counts().sort_index())

# Drift
drift = load_drift(journal_path)
if not drift.empty:
    st.subheader("Turnover drift z-score over time")
    st.line_chart(drift.set_index("ts")["z_score"])

# Trade table at bottom
st.subheader("All trades")
st.dataframe(
    trades[
        [
            "entry_ts", "exit_ts", "regime", "side", "exit_reason",
            "entry_price", "exit_price", "pnl_dollars",
            "slippage_ticks", "entry_slip_ticks",
        ]
    ].sort_values("entry_ts"),
    use_container_width=True,
)

st.caption(
    f"Generated {datetime.now().isoformat(timespec='seconds')} from "
    f"`{journal_path}` — {n_trades} trades, {len(counters)} event types in journal."
)
