"""Tests for ``scripts/_promotion_gate.py`` (H4 closure).

Pin the contract the Red Team review demanded:

  > Encode the 9 promotion gates as deterministic pass/fail checks.
  > A gate failure (or NO_DATA) MUST block live promotion. No
  > override flag in the gate -- failure is structural.

Covers:
  * Each gate has the expected name, evaluator, and PASS criterion
  * --gate <name> exit codes (0 PASS, 1 FAIL, 2 NO_DATA)
  * --all aggregate exit code (0 only when ALL pass)
  * NO_DATA counts as HOLD in the aggregate
  * No --override flag exists
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / "scripts" / "_promotion_gate.py"


@pytest.fixture(scope="module")
def gate_mod():
    spec = importlib.util.spec_from_file_location(
        "promotion_gate_for_test", GATE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["promotion_gate_for_test"] = module
    spec.loader.exec_module(module)
    return module


def test_nine_gates_registered(gate_mod) -> None:
    """Pin the gate count + names so a future refactor that drops a
    gate is caught immediately."""
    expected = {
        "walk_forward_ci_low",
        "block_bootstrap_ci_low",
        "dsr_search",
        "psr_deployment",
        "n_trades_min",
        "regime_stability",
        "dow_filter_placebo",
        "knob_wf_sensitivity",
        "paper_soak_min_weeks",
    }
    assert set(gate_mod._GATE_NAMES) == expected
    assert len(gate_mod._GATE_NAMES) == 9


def test_each_gate_has_evaluator(gate_mod) -> None:
    """Every registered gate name maps to a callable that returns a
    GateResult."""
    for name, fn in gate_mod._GATES:
        assert callable(fn), f"{name} evaluator is not callable"


def test_aggregate_verdict_ordering(gate_mod) -> None:
    PASS, FAIL, NO_DATA = gate_mod.PASS, gate_mod.FAIL, gate_mod.NO_DATA
    G = gate_mod.GateResult

    # All PASS -> 0
    all_pass = [G("a", PASS, "", {}), G("b", PASS, "", {})]
    assert gate_mod.aggregate_verdict(all_pass) == 0

    # Any FAIL -> 1, even if others NO_DATA
    with_fail = [
        G("a", PASS, "", {}),
        G("b", FAIL, "", {}),
        G("c", NO_DATA, "", {}),
    ]
    assert gate_mod.aggregate_verdict(with_fail) == 1

    # NO_DATA only (no FAIL) -> 2
    with_no_data = [G("a", PASS, "", {}), G("b", NO_DATA, "", {})]
    assert gate_mod.aggregate_verdict(with_no_data) == 2

    # Empty -> 0 (vacuously all pass; not expected in practice)
    assert gate_mod.aggregate_verdict([]) == 0


def test_main_all_returns_aggregate(
    gate_mod, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """--all returns the aggregate exit code."""
    PASS, FAIL = gate_mod.PASS, gate_mod.FAIL
    G = gate_mod.GateResult

    # Stub evaluate_all to return mixed results
    monkeypatch.setattr(
        gate_mod, "_GATES",
        [
            ("g1", lambda: G("g1", PASS, "ok", {})),
            ("g2", lambda: G("g2", FAIL, "broken", {})),
        ],
    )
    monkeypatch.setattr(gate_mod, "_GATE_NAMES", ["g1", "g2"])
    rc = gate_mod.main(["--all"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "PASS" in captured.out
    assert "FAIL" in captured.out


def test_main_single_gate_returns_individual_verdict(
    gate_mod, monkeypatch: pytest.MonkeyPatch,
) -> None:
    PASS = gate_mod.PASS
    G = gate_mod.GateResult
    monkeypatch.setattr(
        gate_mod, "_GATES",
        [("g1", lambda: G("g1", PASS, "ok", {}))],
    )
    monkeypatch.setattr(gate_mod, "_GATE_NAMES", ["g1"])
    rc = gate_mod.main(["--gate", "g1"])
    assert rc == 0


def test_main_unknown_gate_rejected(gate_mod) -> None:
    """argparse choices reject unknown gate names with exit 2 +
    stderr message."""
    with pytest.raises(SystemExit) as exc:
        gate_mod.main(["--gate", "totally_made_up"])
    # argparse's error -> exit code 2
    assert exc.value.code == 2


def test_main_requires_gate_or_all(gate_mod) -> None:
    """--gate and --all are mutually exclusive AND one must be given."""
    with pytest.raises(SystemExit) as exc:
        gate_mod.main([])
    assert exc.value.code == 2  # argparse-required-arg failure


def test_main_json_output_shape(
    gate_mod, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    PASS = gate_mod.PASS
    G = gate_mod.GateResult
    monkeypatch.setattr(
        gate_mod, "_GATES",
        [("g1", lambda: G("g1", PASS, "ok", {"foo": 1}))],
    )
    monkeypatch.setattr(gate_mod, "_GATE_NAMES", ["g1"])

    gate_mod.main(["--all", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["rc"] == 0
    assert payload["n_pass"] == 1
    assert payload["gates"][0]["name"] == "g1"
    assert payload["gates"][0]["verdict"] == "PASS"


def test_no_override_flag_exists(gate_mod) -> None:
    """The Red Team contract: 'no override flag in the gate'.

    Pin that no CLI option named --override / --force / --skip /
    --no-fail / --advisory exists. If a future refactor adds one,
    this test fails and the operator/reviewer must justify it.
    """
    forbidden = {"--override", "--force", "--skip", "--no-fail", "--advisory"}
    # Inspect argparse via the module's main signature is hard; build
    # the parser manually to introspect.
    # Easier: invoke main(["--help"]) and assert no forbidden flag
    # appears in the help text. argparse exits with 0 on --help.
    import contextlib as ctx
    import io
    buf = io.StringIO()
    with ctx.redirect_stdout(buf), pytest.raises(SystemExit) as exc:
        gate_mod.main(["--help"])
    assert exc.value.code == 0
    help_text = buf.getvalue()
    for flag in forbidden:
        assert flag not in help_text, (
            f"forbidden override flag {flag!r} appears in promotion-gate "
            f"--help. The Red Team contract requires no override."
        )
