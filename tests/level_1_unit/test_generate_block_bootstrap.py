"""Tests for ``scripts/generate_block_bootstrap.py`` -- v0.2.23
artifact generator that produces ``reports/block_bootstrap.json``
for the v0.2.4 promotion gate.

Pin the contract:

  * Missing journal -> writes a 0-trade artifact (gate reads it as FAIL)
  * Journal with paired fills -> writes the block-bootstrap CI
  * --variant filter restricts to that variant's fills
  * --k / --block / --seed flags pass through
  * Output file has all the keys the gate evaluator reads
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "generate_block_bootstrap.py"


@pytest.fixture(scope="module")
def gen_mod():
    spec = importlib.util.spec_from_file_location(
        "generate_block_bootstrap_for_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_block_bootstrap_for_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# _read_per_trade_r
# ---------------------------------------------------------------------------


def test_missing_journal_returns_empty(gen_mod, tmp_path: Path) -> None:
    rs = gen_mod._read_per_trade_r(tmp_path / "missing.sqlite")
    assert rs == []


def test_empty_journal_returns_empty(gen_mod, tmp_path: Path) -> None:
    """Journal exists but has no FILL_REALIZED events."""
    from mnq.storage.journal import EventJournal

    journal_path = tmp_path / "empty.sqlite"
    EventJournal(journal_path)  # initializes schema, no events
    rs = gen_mod._read_per_trade_r(journal_path)
    assert rs == []


def test_journal_with_paired_fills_yields_r_multiples(
    gen_mod,
    tmp_path: Path,
) -> None:
    """Two fills with same client_order_id -> one trade. Long entry
    at 21000, exit at 21010 = +10 points = +$20 = +$20/$5 = +4R."""
    from mnq.storage.journal import EventJournal
    from mnq.storage.schema import FILL_REALIZED

    journal_path = tmp_path / "j.sqlite"
    j = EventJournal(journal_path)
    cid = "trade_001"
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": cid,
            "side": "long",
            "qty": 1,
            "price": "21000.0",
        },
    )
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": cid,
            "side": "short",  # opposite to close
            "qty": 1,
            "price": "21010.0",
        },
    )
    rs = gen_mod._read_per_trade_r(journal_path)
    assert len(rs) == 1
    # +10 points * $2/point = $20; risk_dollars = 10 ticks * $0.50 = $5
    # -> 4.0R
    assert rs[0] == pytest.approx(4.0)


def test_journal_short_trade_sign_flipped(gen_mod, tmp_path: Path) -> None:
    """Short entry at 21010, exit at 21000 -> +10 points DOWN = WIN -> +R."""
    from mnq.storage.journal import EventJournal
    from mnq.storage.schema import FILL_REALIZED

    journal_path = tmp_path / "j.sqlite"
    j = EventJournal(journal_path)
    cid = "short_001"
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": cid,
            "side": "short",
            "qty": 1,
            "price": "21010.0",
        },
    )
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": cid,
            "side": "long",
            "qty": 1,
            "price": "21000.0",
        },
    )
    rs = gen_mod._read_per_trade_r(journal_path)
    assert len(rs) == 1
    assert rs[0] > 0  # short went down -> profit


def test_variant_filter_restricts(gen_mod, tmp_path: Path) -> None:
    """Only fills with matching variant tag are counted."""
    from mnq.storage.journal import EventJournal
    from mnq.storage.schema import FILL_REALIZED

    journal_path = tmp_path / "j.sqlite"
    j = EventJournal(journal_path)
    # Trade A: variant=v_a
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": "a1",
            "side": "long",
            "qty": 1,
            "price": "21000.0",
            "variant": "v_a",
        },
    )
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": "a1",
            "side": "short",
            "qty": 1,
            "price": "21010.0",
            "variant": "v_a",
        },
    )
    # Trade B: variant=v_b
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": "b1",
            "side": "long",
            "qty": 1,
            "price": "21000.0",
            "variant": "v_b",
        },
    )
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": "b1",
            "side": "short",
            "qty": 1,
            "price": "21005.0",
            "variant": "v_b",
        },
    )
    rs_a = gen_mod._read_per_trade_r(journal_path, variant_filter="v_a")
    rs_b = gen_mod._read_per_trade_r(journal_path, variant_filter="v_b")
    rs_all = gen_mod._read_per_trade_r(journal_path)
    assert len(rs_a) == 1
    assert len(rs_b) == 1
    assert len(rs_all) == 2


def test_singleton_fill_skipped(gen_mod, tmp_path: Path) -> None:
    """A client_order_id with only one fill (entry, no exit) is
    skipped -- can't compute R-multiple."""
    from mnq.storage.journal import EventJournal
    from mnq.storage.schema import FILL_REALIZED

    journal_path = tmp_path / "j.sqlite"
    j = EventJournal(journal_path)
    j.append(
        FILL_REALIZED,
        {
            "client_order_id": "open_only",
            "side": "long",
            "qty": 1,
            "price": "21000.0",
        },
    )
    rs = gen_mod._read_per_trade_r(journal_path)
    assert rs == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_writes_artifact_with_required_keys(
    gen_mod,
    tmp_path: Path,
) -> None:
    """The artifact must have every key the gate evaluator reads."""
    output = tmp_path / "out.json"
    rc = gen_mod.main(
        [
            "--journal",
            str(tmp_path / "missing.sqlite"),
            "--output",
            str(output),
            "--k",
            "100",
        ]
    )
    assert rc == 0
    assert output.exists()
    data = json.loads(output.read_text())
    # Required for the v0.2.4 gate evaluator
    assert "ci95_low" in data
    assert "ci95_high" in data
    assert "n_trades" in data
    assert "k" in data
    assert "block_size" in data


def test_main_zero_trades_writes_zero_artifact(
    gen_mod,
    tmp_path: Path,
    capsys,
) -> None:
    """Empty journal -> artifact has n_trades=0 + ci95_low=0.0.
    Gate transitions from NO_DATA (missing) to FAIL (CI95 < 0.05R)."""
    output = tmp_path / "out.json"
    gen_mod.main(
        [
            "--journal",
            str(tmp_path / "missing.sqlite"),
            "--output",
            str(output),
        ]
    )
    data = json.loads(output.read_text())
    assert data["n_trades"] == 0
    assert data["ci95_low"] == 0.0
    captured = capsys.readouterr()
    assert "0 trades" in captured.err.lower() or "n_trades=0" in captured.out


def test_main_passes_seed_for_determinism(
    gen_mod,
    tmp_path: Path,
) -> None:
    """Same seed -> same artifact bytes."""
    from mnq.storage.journal import EventJournal
    from mnq.storage.schema import FILL_REALIZED

    journal_path = tmp_path / "j.sqlite"
    j = EventJournal(journal_path)
    # 30 alternating wins/losses
    for i in range(15):
        cid = f"t_{i}"
        j.append(
            FILL_REALIZED,
            {
                "client_order_id": cid,
                "side": "long",
                "qty": 1,
                "price": "21000.0",
            },
        )
        # Win on even, loss on odd
        exit_price = "21010.0" if i % 2 == 0 else "20995.0"
        j.append(
            FILL_REALIZED,
            {
                "client_order_id": cid,
                "side": "short",
                "qty": 1,
                "price": exit_price,
            },
        )

    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    gen_mod.main(
        [
            "--journal",
            str(journal_path),
            "--output",
            str(out_a),
            "--k",
            "200",
            "--seed",
            "11",
        ]
    )
    gen_mod.main(
        [
            "--journal",
            str(journal_path),
            "--output",
            str(out_b),
            "--k",
            "200",
            "--seed",
            "11",
        ]
    )
    a = json.loads(out_a.read_text())
    b = json.loads(out_b.read_text())
    assert a["ci95_low"] == b["ci95_low"]
    assert a["ci95_high"] == b["ci95_high"]


def test_main_threshold_passes_to_artifact(
    gen_mod,
    tmp_path: Path,
) -> None:
    """--threshold-r 0.10 should land in the artifact's paper_gate_r."""
    output = tmp_path / "out.json"
    gen_mod.main(
        [
            "--journal",
            str(tmp_path / "missing.sqlite"),
            "--output",
            str(output),
            "--threshold-r",
            "0.10",
        ]
    )
    data = json.loads(output.read_text())
    assert data["paper_gate_r"] == 0.10
