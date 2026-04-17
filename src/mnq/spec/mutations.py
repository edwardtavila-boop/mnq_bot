"""[CONTRACT] GA mutation operators on StrategySpec.

Required operators (each takes a parent spec, returns a new spec or None):

    mutate_param(spec, rng) -> StrategySpec | None
        Pick a random numeric leaf (feature length, ATR multiplier, risk
        param, etc.), perturb within bounds defined per-field. Bounds live
        in `_FIELD_BOUNDS` (to be defined). Returns None if no mutable
        params or if the chosen perturbation would violate constraints.

    mutate_comparator(spec, rng) -> StrategySpec | None
        Pick a Comparison node in entry conditions; swap '>' <-> '>=', etc.,
        only where semantically valid (don't swap 'crosses_above' to '>').

    mutate_clause_add(spec, rng) -> StrategySpec | None
        Add a randomly-selected clause to entry.long.all_of (and mirror to
        short via the mirror_of mechanism). New clauses are sampled from a
        catalog of legal templates.

    mutate_clause_remove(spec, rng) -> StrategySpec | None
        Remove one clause from entry.long.all_of (must leave >= 2).

    mutate_relax_to_n_of(spec, rng) -> StrategySpec | None
        Convert all_of: [A,B,C,D] to n_of: (k, [A,B,C,D]) with k = len-1
        or len-2.

    mutate_feature_swap(spec, rng) -> StrategySpec | None
        Replace a feature with another of the same type (EMA(9) -> SMA(9)).
        References to the feature in conditions update automatically.

    mutate_session_window(spec, rng) -> StrategySpec | None
        Toggle one session window's enabled flag.

    mutate_risk(spec, rng) -> StrategySpec | None
        Tweak a risk parameter within bounds.

Plus crossover:

    crossover(parent_a, parent_b, rng) -> StrategySpec | None
        Combine condition lists, feature sets, and risk params from two
        parents. The child must validate. If features are referenced that
        don't survive crossover, prune the orphan references first.

All operators MUST:
- Return a spec that passes `validate_spec()` or return None.
- Update `strategy.parent_hash` to the parent's content_hash.
- Reset `strategy.created_by` to "rl_agent_v<N>".
- Reset `strategy.created_at` to now_utc().
- Reset `strategy.tier` to "sim".
- Reset `strategy.semver` (bump patch from parent: 0.4.2 -> 0.4.3).
- Clear `strategy.content_hash` (will be re-stamped by stamp_hash()).
- Reset `provenance` (the new spec hasn't been validated yet).

The full mutation catalog (`_FIELD_BOUNDS`, `_CLAUSE_TEMPLATES`) is its
own decision — keep it small and deliberate. Each new template the agent
can sample is part of its action space and should be intentional.
"""
from __future__ import annotations

from random import Random

from mnq.spec.schema import StrategySpec


def mutate_param(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def mutate_comparator(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def mutate_clause_add(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def mutate_clause_remove(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def mutate_relax_to_n_of(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def mutate_feature_swap(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def mutate_session_window(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def mutate_risk(spec: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")


def crossover(parent_a: StrategySpec, parent_b: StrategySpec, rng: Random) -> StrategySpec | None:
    raise NotImplementedError("[CONTRACT] see module docstring")
