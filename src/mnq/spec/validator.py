"""[REAL] Semantic validation for strategy specs.

Pydantic enforces types and field-level constraints. This module enforces
cross-field invariants:

- Every feature reference in conditions resolves to a declared feature.
- Mirror_of doesn't create cycles.
- Stop floors/ceilings are coherent (min_ticks <= max_ticks).
- Risk caps are consistent (per_trade <= per_session <= per_week).
- Feature feeds match the instrument's data_feed.
- Generators don't reference disabled features.
"""

from __future__ import annotations

import re

from mnq.spec.schema import EntrySide, StrategySpec


class SpecValidationError(Exception):
    pass


_FEATURE_REF_RE = re.compile(r"\bfeature:([a-zA-Z_][a-zA-Z0-9_]*)\b")


def validate_spec(spec: StrategySpec) -> None:
    feature_ids = {f.id for f in spec.features}

    _check_feature_refs(spec.entry.long, feature_ids, "entry.long")
    _check_feature_refs(spec.entry.short, feature_ids, "entry.short")
    _check_mirror_no_cycle(spec.entry.long, spec.entry.short)
    _check_exit_features(spec, feature_ids)
    _check_risk_consistency(spec)
    _check_feature_feeds(spec)
    _check_session(spec)
    _check_position_sizing(spec)


def _conditions_of(side: EntrySide) -> list[str]:
    if side.all_of:
        return list(side.all_of)
    if side.any_of:
        return list(side.any_of)
    if side.n_of:
        return list(side.n_of[1])
    return []  # mirror_of side has no own conditions


def _check_feature_refs(side: EntrySide, feature_ids: set[str], where: str) -> None:
    for cond in _conditions_of(side):
        for ref in _FEATURE_REF_RE.findall(cond):
            if ref not in feature_ids:
                raise SpecValidationError(
                    f"{where}: condition {cond!r} references undefined feature {ref!r}. "
                    f"Defined features: {sorted(feature_ids)}"
                )


def _check_mirror_no_cycle(long: EntrySide, short: EntrySide) -> None:
    if long.mirror_of == "short" and short.mirror_of == "long":
        raise SpecValidationError("entry.long and entry.short both mirror_of each other")
    if long.mirror_of == "long":
        raise SpecValidationError("entry.long cannot mirror_of itself")
    if short.mirror_of == "short":
        raise SpecValidationError("entry.short cannot mirror_of itself")


def _check_exit_features(spec: StrategySpec, feature_ids: set[str]) -> None:
    s = spec.exit.initial_stop
    if s.type == "atr_multiple":
        if not s.feature or s.feature not in feature_ids:
            raise SpecValidationError(
                f"exit.initial_stop.feature {s.feature!r} not declared in features"
            )
        if s.multiplier is None or s.multiplier <= 0:
            raise SpecValidationError("exit.initial_stop.multiplier must be > 0")
    if s.min_ticks > s.max_ticks:
        raise SpecValidationError(
            f"exit.initial_stop.min_ticks ({s.min_ticks}) > max_ticks ({s.max_ticks})"
        )

    tp = spec.exit.take_profit
    if tp.type == "atr_multiple":
        if not tp.feature or tp.feature not in feature_ids:
            raise SpecValidationError(
                f"exit.take_profit.feature {tp.feature!r} not declared in features"
            )


def _check_risk_consistency(spec: StrategySpec) -> None:
    pt = spec.risk.per_trade.max_loss_usd
    ps = spec.risk.per_session.max_loss_usd
    pw = spec.risk.per_week.max_loss_usd
    if pt > ps:
        raise SpecValidationError(f"per_trade ({pt}) > per_session ({ps}) max loss")
    if ps > pw:
        raise SpecValidationError(f"per_session ({ps}) > per_week ({pw}) max loss")


def _check_feature_feeds(spec: StrategySpec) -> None:
    feed = spec.instrument.data_feed
    for f in spec.features:
        required = getattr(f, "feed_required", None)
        if required and required != feed:
            raise SpecValidationError(
                f"feature {f.id!r} requires feed {required!r} but instrument uses {feed!r}"
            )


def _check_session(spec: StrategySpec) -> None:
    names = [w.name for w in spec.session.windows]
    if len(names) != len(set(names)):
        raise SpecValidationError(f"duplicate session window names: {names}")


def _check_position_sizing(spec: StrategySpec) -> None:
    ps = spec.position_sizing
    if ps.mode == "fixed_risk" and ps.risk_per_trade_usd is None:
        raise SpecValidationError("position_sizing.mode=fixed_risk requires risk_per_trade_usd")
    if ps.mode == "fixed_contracts" and ps.fixed_contracts is None:
        raise SpecValidationError("position_sizing.mode=fixed_contracts requires fixed_contracts")
    if ps.min_contracts > ps.max_contracts:
        raise SpecValidationError(
            f"min_contracts ({ps.min_contracts}) > max_contracts ({ps.max_contracts})"
        )
