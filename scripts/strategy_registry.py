"""Strategy registry — content-addressed config hashing + graveyard.

Cross-cutting roadmap work. Every variant we run should be tagged with a
stable hash of its ``StrategyConfig`` so that:

* Every trade journaled under that hash can be attributed back to exactly
  one config.
* A retired variant can be buried in ``reports/strategy_graveyard.md``
  with its final numbers and falsification reason, so we don't
  accidentally revive a dead idea.
* A config change to a live variant is caught automatically — same name,
  new hash = new entry.

Usage:

    python scripts/strategy_registry.py --update        # refresh registry
    python scripts/strategy_registry.py --bury t17_r5_short_only \\
         --reason "sample size too small; CI crosses zero"
    python scripts/strategy_registry.py --list
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from strategy_v2 import VARIANTS as _VARIANT_LIST  # noqa: E402

REGISTRY_PATH = REPO_ROOT / "reports" / "strategy_registry.json"
GRAVEYARD_PATH = REPO_ROOT / "reports" / "strategy_graveyard.md"


def _cfg_to_dict(cfg: object) -> dict:
    """Dataclass → JSON-serializable dict; tuples become lists, Decimals strs."""
    if not dataclasses.is_dataclass(cfg):
        raise TypeError(f"expected a dataclass config, got {type(cfg).__name__}")
    d = asdict(cfg)
    return _canonicalize(d)


def _canonicalize(obj):
    if isinstance(obj, dict):
        return {k: _canonicalize(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(v) for v in obj]
    if isinstance(obj, float) and obj != obj:  # NaN
        return "NaN"
    return obj


def config_hash(cfg: object) -> str:
    """Short stable hash of a StrategyConfig — 12 hex chars is plenty."""
    d = _cfg_to_dict(cfg)
    payload = json.dumps(d, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def build_registry() -> dict:
    """Walk VARIANTS, hash each, produce a registry dict ready to serialize."""
    out: dict = {
        "generated": datetime.now(UTC).isoformat(),
        "variants": {},
    }
    for cfg in _VARIANT_LIST:
        name = cfg.name
        out["variants"][name] = {
            "hash": config_hash(cfg),
            "config": _cfg_to_dict(cfg),
        }
    return out


def load_registry() -> dict:
    """Load the persisted registry, tolerant of corrupted JSON.

    If the file is missing OR was truncated by an external sync
    (OneDrive) and can no longer parse, return an empty dict so
    ``--update`` can overwrite cleanly rather than aborting.
    """
    if not REGISTRY_PATH.exists():
        return {"variants": {}}
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except json.JSONDecodeError as e:
        print(
            f"! strategy_registry.json corrupted ({e}); treating as "
            f"empty and rebuilding from scratch",
            file=sys.stderr,
        )
        return {"variants": {}}


def save_registry(reg: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2, sort_keys=True))


def detect_drift(prior: dict, current: dict) -> list[tuple[str, str, str]]:
    """Return (variant_name, old_hash, new_hash) tuples for any config that
    was already registered but now hashes to something different."""
    drifts: list[tuple[str, str, str]] = []
    prior_variants = prior.get("variants", {})
    for name, entry in current.get("variants", {}).items():
        old = prior_variants.get(name)
        if old and old.get("hash") != entry["hash"]:
            drifts.append((name, old["hash"], entry["hash"]))
    return drifts


def bury(name: str, reason: str) -> None:
    """Append a row to the graveyard markdown, permanently retiring a variant."""
    reg = load_registry()
    entry = reg.get("variants", {}).get(name)
    if entry is None:
        raise KeyError(f"unknown variant: {name}")
    h = entry["hash"]
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    header_needed = not GRAVEYARD_PATH.exists()
    GRAVEYARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GRAVEYARD_PATH.open("a") as fh:
        if header_needed:
            fh.write(
                "# Strategy Graveyard\n\n"
                "Retired variants — never resurrect without re-running the "
                "Firm review.\n\n"
                "| Date | Variant | Hash | Reason |\n"
                "|---|---|---|---|\n"
            )
        fh.write(f"| {now} | `{name}` | `{h}` | {reason} |\n")


def _render_registry_table(reg: dict) -> str:
    lines = ["# Strategy Registry", ""]
    lines.append(f"- Generated: {reg.get('generated', '?')}")
    lines.append(f"- Variants: **{len(reg.get('variants', {}))}**")
    lines.append("")
    lines.append("| Variant | Hash | Key knobs |")
    lines.append("|---|---|---|")
    for name, entry in sorted(reg.get("variants", {}).items()):
        cfg = entry["config"]
        summary = (
            f"rr={cfg.get('rr')}, "
            f"vol_max={cfg.get('vol_filter_stdev_max')}, "
            f"hard_pause={cfg.get('vol_hard_pause_stdev')}, "
            f"allow_long={cfg.get('allow_long')}, "
            f"allow_short={cfg.get('allow_short')}"
        )
        lines.append(f"| `{name}` | `{entry['hash']}` | {summary} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy registry + graveyard.")
    parser.add_argument("--update", action="store_true", help="Rebuild the registry.")
    parser.add_argument("--list", action="store_true", help="Print registry summary.")
    parser.add_argument("--bury", type=str, default=None, help="Retire variant by name.")
    parser.add_argument("--reason", type=str, default="", help="Reason for burial.")
    args = parser.parse_args(argv)

    if args.bury:
        if not args.reason:
            print("--bury requires --reason", file=sys.stderr)
            return 2
        bury(args.bury, args.reason)
        print(f"buried `{args.bury}` in {GRAVEYARD_PATH}")
        return 0

    current = build_registry()

    if args.update or not REGISTRY_PATH.exists():
        prior = load_registry()
        drifts = detect_drift(prior, current)
        save_registry(current)
        print(f"wrote {REGISTRY_PATH}")
        if drifts:
            print("CONFIG DRIFT DETECTED:")
            for name, old, new in drifts:
                print(f"  {name}: {old} → {new}")
        # Also refresh the markdown index for human consumption
        md_path = REPO_ROOT / "reports" / "strategy_registry.md"
        md_path.write_text(_render_registry_table(current))
        print(f"wrote {md_path}")

    if args.list:
        print(_render_registry_table(current))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
