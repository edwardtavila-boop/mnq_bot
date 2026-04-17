"""AUTO-GENERATED from spec mnq_baseline_v0_1 v0.1.0.

DO NOT EDIT BY HAND. Regenerate via:
    mnq spec render specs/strategies/mnq_baseline_v0_1.yaml --target python

content_hash: sha256:3101335bb6f6493cb228259d0d0bde13591acd352c458700f89e97de9f584b39
"""
from __future__ import annotations

from mnq.features import ATR, EMA, HTFWrapper, RMA, RelativeVolume, SMA, VWAP
from mnq.generators.python_exec.base import BarCtx, HistoryRing, StrategyBase

SPEC_HASH = 'sha256:3101335bb6f6493cb228259d0d0bde13591acd352c458700f89e97de9f584b39'
SPEC_ID = 'mnq_baseline_v0_1'
SPEC_SEMVER = '0.1.0'


# ---- AST-level comparison helpers ----
# These are used by the generated _eval_* methods.  They live at module
# scope so they're import-time cheap and deterministic.

def _resolve(spec_tuple, ctx, idx):
    """Resolve a (kind, key) spec to the scalar value at bar-offset idx.

    `idx=0` = current bar; `idx=1` = one bar ago, etc.  Returns None if
    the value isn't available yet.
    """
    kind, key = spec_tuple
    if kind == 'n':
        return key
    if kind == 'f':
        ring = ctx.hist(key)
        return ring[idx]
    if kind == 'b':
        if idx == 0:
            from mnq.features._source import price_from_source
            return price_from_source(ctx.bar, key)
        # past builtin: we don't keep bar history, so fall back to None
        return None
    return None


def _cmp(a, b, op):
    if a is None or b is None:
        return False
    if op == '>': return a > b
    if op == '<': return a < b
    if op == '>=': return a >= b
    if op == '<=': return a <= b
    if op == '==': return a == b
    if op == '!=': return a != b
    return False


def _for_bars_cmp(left_spec, right_spec, op, n, ctx):
    for i in range(n):
        a = _resolve(left_spec, ctx, i)
        b = _resolve(right_spec, ctx, i)
        if not _cmp(a, b, op):
            return False
    return True


def _on_bar_cmp(left_spec, right_spec, op, n, ctx):
    return _cmp(_resolve(left_spec, ctx, n), _resolve(right_spec, ctx, n), op)


def _crossed_above(left_spec, right_spec, within, ctx):
    for i in range(within):
        a_now, a_prev = _resolve(left_spec, ctx, i), _resolve(left_spec, ctx, i + 1)
        b_now, b_prev = _resolve(right_spec, ctx, i), _resolve(right_spec, ctx, i + 1)
        if None in (a_now, a_prev, b_now, b_prev):
            continue
        if a_prev <= b_prev and a_now > b_now:
            return True
    return False


def _crossed_below(left_spec, right_spec, within, ctx):
    for i in range(within):
        a_now, a_prev = _resolve(left_spec, ctx, i), _resolve(left_spec, ctx, i + 1)
        b_now, b_prev = _resolve(right_spec, ctx, i), _resolve(right_spec, ctx, i + 1)
        if None in (a_now, a_prev, b_now, b_prev):
            continue
        if a_prev >= b_prev and a_now < b_now:
            return True
    return False


def _crossed(left_spec, right_spec, within, ctx):
    return (_crossed_above(left_spec, right_spec, within, ctx)
            or _crossed_below(left_spec, right_spec, within, ctx))


class GeneratedStrategy(StrategyBase):
    """Generated from mnq_baseline_v0_1 v0.1.0."""

    def _eval_long(self, ctx: BarCtx) -> bool:
        try:
            return bool(((ctx.f('ema_fast') > ctx.f('ema_slow')) and _crossed_above(('f', 'ema_fast'), ('f', 'ema_slow'), 3, ctx) and (float(ctx.bar.close) > ctx.f('vwap_session')) and ctx.hist('htf_trend').rising(2) and (ctx.f('rvol_20') > 1.2) and (ctx.session_window in ('rth_open_drive', 'afternoon')) and (not ctx.in_blackout) and (ctx.position_size == 0)))
        except RuntimeError:
            # A feature referenced by the condition isn't ready yet.
            return False

    def _eval_short(self, ctx: BarCtx) -> bool:
        try:
            return bool(((ctx.f('ema_fast') < ctx.f('ema_slow')) and _crossed_below(('f', 'ema_fast'), ('f', 'ema_slow'), 3, ctx) and (float(ctx.bar.close) < ctx.f('vwap_session')) and ctx.hist('htf_trend').falling(2) and (ctx.f('rvol_20') < 1.2) and (ctx.session_window in ('rth_open_drive', 'afternoon')) and (not ctx.in_blackout) and (ctx.position_size == 0)))
        except RuntimeError:
            return False

    def _compute_stop_ticks(self, ctx: BarCtx) -> int:
        try:
            return int(round((ctx.f('atr_14') * 1.2) / 0.25))
        except RuntimeError:
            return self.spec.exit.initial_stop.min_ticks


def build(spec) -> GeneratedStrategy:
    """Wire a GeneratedStrategy from the given StrategySpec instance.

    The spec is assumed to equal the one this module was generated from;
    the executor verifies `spec.strategy.content_hash == SPEC_HASH` before
    calling.
    """
    if spec.strategy.content_hash and SPEC_HASH and spec.strategy.content_hash != SPEC_HASH:
        raise ValueError(
            f"spec hash {spec.strategy.content_hash} != generator hash {SPEC_HASH}"
        )
    features = {
        'ema_fast': EMA(length=9, source='close'),
        'ema_slow': EMA(length=21, source='close'),
        'atr_14': ATR(length=14),
        'vwap_session': VWAP(anchor='session'),
        'rvol_20': RelativeVolume(length=20),
        'htf_trend': HTFWrapper(EMA(length=50, source='close'), timeframe='5m')
    }
    return GeneratedStrategy(spec=spec, features=features)
