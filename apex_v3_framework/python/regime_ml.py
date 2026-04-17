"""
Apex v2 ML Regime Classifier
============================
Trains a Random Forest to classify market regime (RISK-ON / RISK-OFF /
NEUTRAL / CRISIS) from indicator features. The rule-based detector in
firm_engine.detect_regime() is the baseline; this ML model can be plugged
in as a drop-in replacement once trained on enough data.

Workflow:
  1. python regime_ml.py train mnq_5m.csv [--out model.pkl]
     → loads bars, runs IndicatorState, labels with rule-based regime,
       trains RandomForest, saves model.

  2. In production: load model and replace `detect_regime()` calls with
     `MLRegime(model_path).predict(bar, state)`.

Features used (all per-bar):
  - adx, atr_ratio, vol_z, rsi, range_expand, htf_dist
  - ema9_slope, ema21_slope, distance from EMA50
"""

import argparse
import pickle
from pathlib import Path
from typing import List, Optional

try:
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, confusion_matrix
except ImportError:
    raise SystemExit("Install sklearn: pip install -r requirements.txt")

from firm_engine import Bar, detect_regime
from indicator_state import IndicatorState
from backtest import load_csv


REGIME_LABELS = ["RISK-ON", "RISK-OFF", "NEUTRAL", "CRISIS"]
LABEL_TO_IDX = {r: i for i, r in enumerate(REGIME_LABELS)}
IDX_TO_LABEL = {i: r for r, i in LABEL_TO_IDX.items()}


def _safe(v, default=0.0):
    return v if v is not None else default


def features_from_bar(bar: Bar, state: IndicatorState,
                      prev_ema9: Optional[float],
                      prev_ema21: Optional[float]) -> List[float]:
    """Extract feature vector for ML regime classifier."""
    atr = _safe(bar.atr)
    atr_ma20 = state.atr_ma20() or 1.0
    atr_ratio = atr / atr_ma20 if atr_ma20 > 0 else 1.0

    rng = bar.high - bar.low
    range_avg = state.range_avg_20() or 1.0
    range_expand = rng / range_avg if range_avg > 0 else 1.0

    htf_dist = 0.0
    if bar.htf_close is not None and bar.htf_ema50 is not None and atr > 0:
        htf_dist = (bar.htf_close - bar.htf_ema50) / atr

    ema9_slope = 0.0
    ema21_slope = 0.0
    if prev_ema9 is not None and bar.ema9 is not None:
        ema9_slope = (bar.ema9 - prev_ema9) / atr if atr > 0 else 0
    if prev_ema21 is not None and bar.ema21 is not None:
        ema21_slope = (bar.ema21 - prev_ema21) / atr if atr > 0 else 0

    ema_spread = 0.0
    if bar.ema9 is not None and bar.ema21 is not None and atr > 0:
        ema_spread = (bar.ema9 - bar.ema21) / atr

    return [
        _safe(bar.adx, 20.0),
        atr_ratio,
        state.vol_z(),
        _safe(bar.rsi, 50.0),
        range_expand,
        htf_dist,
        ema9_slope,
        ema21_slope,
        ema_spread,
    ]


FEATURE_NAMES = [
    "adx", "atr_ratio", "vol_z", "rsi", "range_expand",
    "htf_dist", "ema9_slope", "ema21_slope", "ema_spread",
]


def build_training_set(csv_path: str):
    print(f"Loading {csv_path}...")
    bars = load_csv(csv_path)
    print(f"Loaded {len(bars)} bars")

    state = IndicatorState()
    X = []
    y = []
    prev_ema9 = None
    prev_ema21 = None

    for bar in bars:
        state.update(bar)
        # Need warmup
        if state._adx is None or state._atr is None or state.atr_ma20() == 0:
            prev_ema9 = bar.ema9
            prev_ema21 = bar.ema21
            continue

        feats = features_from_bar(bar, state, prev_ema9, prev_ema21)
        regime = detect_regime(bar.adx or 20, bar.atr or 0,
                               state.atr_ma20(), state.vol_z())
        X.append(feats)
        y.append(LABEL_TO_IDX[regime])
        prev_ema9 = bar.ema9
        prev_ema21 = bar.ema21

    return np.array(X), np.array(y)


def train(csv_path: str, out_path: str):
    X, y = build_training_set(csv_path)
    print(f"\nTraining set: {len(X)} samples, {X.shape[1]} features")
    print(f"Class distribution:")
    for i, lbl in enumerate(REGIME_LABELS):
        cnt = (y == i).sum()
        print(f"  {lbl:10s}: {cnt:6d}  ({cnt/len(y)*100:.1f}%)")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y if len(np.unique(y)) > 1 else None
    )

    print(f"\nTraining RandomForest on {len(X_train)} samples...")
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=20,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_train, y_train)

    # Evaluate
    train_acc = clf.score(X_train, y_train)
    test_acc = clf.score(X_test, y_test)
    print(f"\nTrain accuracy: {train_acc:.3f}")
    print(f"Test accuracy:  {test_acc:.3f}")

    y_pred = clf.predict(X_test)
    present_labels = sorted(set(y_test) | set(y_pred))
    target_names = [IDX_TO_LABEL[i] for i in present_labels]
    print(f"\nClassification report (test set):")
    print(classification_report(y_test, y_pred, labels=present_labels,
                                target_names=target_names, zero_division=0))

    print(f"Confusion matrix (rows=true, cols=pred):")
    cm = confusion_matrix(y_test, y_pred, labels=present_labels)
    header = "          " + "  ".join(f"{n:>9s}" for n in target_names)
    print(header)
    for i, row in enumerate(cm):
        print(f"  {target_names[i]:>8s}  " + "  ".join(f"{v:>9d}" for v in row))

    # Feature importance
    print(f"\nFeature importances:")
    for name, imp in sorted(zip(FEATURE_NAMES, clf.feature_importances_),
                            key=lambda x: -x[1]):
        bar_str = "█" * int(imp * 50)
        print(f"  {name:14s}  {imp:.3f}  {bar_str}")

    # Save model
    out = Path(out_path)
    with out.open("wb") as f:
        pickle.dump({
            "model": clf,
            "feature_names": FEATURE_NAMES,
            "labels": REGIME_LABELS,
            "trained_on": csv_path,
            "train_acc": train_acc,
            "test_acc": test_acc,
        }, f)
    print(f"\nModel saved to {out.absolute()}")


class MLRegime:
    """Drop-in replacement for rule-based detect_regime()."""

    def __init__(self, model_path: str):
        with open(model_path, "rb") as f:
            data = pickle.load(f)
        self.clf = data["model"]
        self.labels = data["labels"]

    def predict(self, bar: Bar, state: IndicatorState,
                prev_ema9: Optional[float] = None,
                prev_ema21: Optional[float] = None) -> str:
        feats = features_from_bar(bar, state, prev_ema9, prev_ema21)
        idx = self.clf.predict([feats])[0]
        return self.labels[idx]

    def predict_proba(self, bar: Bar, state: IndicatorState,
                      prev_ema9=None, prev_ema21=None) -> dict:
        feats = features_from_bar(bar, state, prev_ema9, prev_ema21)
        probs = self.clf.predict_proba([feats])[0]
        return {self.labels[i]: float(p) for i, p in enumerate(probs)}


def main():
    parser = argparse.ArgumentParser(description="Apex v2 ML Regime Classifier")
    sub = parser.add_subparsers(dest="cmd", required=True)

    train_p = sub.add_parser("train", help="Train regime classifier")
    train_p.add_argument("csv", help="OHLCV CSV path")
    train_p.add_argument("--out", default="regime_model.pkl", help="Output model path")

    pred_p = sub.add_parser("predict", help="Show predictions on sample bars")
    pred_p.add_argument("csv", help="OHLCV CSV path")
    pred_p.add_argument("model", help="Trained model path")
    pred_p.add_argument("--n", type=int, default=20, help="Number of last bars to predict")

    args = parser.parse_args()

    if args.cmd == "train":
        train(args.csv, args.out)
    elif args.cmd == "predict":
        bars = load_csv(args.csv)
        ml = MLRegime(args.model)
        state = IndicatorState()
        prev_ema9 = None
        prev_ema21 = None
        from datetime import datetime
        print(f"\nLast {args.n} bars regime predictions vs rule-based:")
        print(f"{'Time':>20s}  {'Close':>9s}  {'Rule-Based':>12s}  {'ML Predict':>12s}  {'Top Probs':>30s}")
        for bar in bars:
            state.update(bar)
            if state._adx is None or state._atr is None or state.atr_ma20() == 0:
                prev_ema9 = bar.ema9
                prev_ema21 = bar.ema21
                continue
        # Re-run for last N
        state = IndicatorState()
        prev_ema9 = None
        prev_ema21 = None
        all_results = []
        for bar in bars:
            state.update(bar)
            if state._adx is None or state._atr is None or state.atr_ma20() == 0:
                prev_ema9 = bar.ema9
                prev_ema21 = bar.ema21
                continue
            rule = detect_regime(bar.adx or 20, bar.atr or 0,
                                state.atr_ma20(), state.vol_z())
            ml_pred = ml.predict(bar, state, prev_ema9, prev_ema21)
            probs = ml.predict_proba(bar, state, prev_ema9, prev_ema21)
            all_results.append((bar.time, bar.close, rule, ml_pred, probs))
            prev_ema9 = bar.ema9
            prev_ema21 = bar.ema21

        for t, c, rule, ml_pred, probs in all_results[-args.n:]:
            top2 = sorted(probs.items(), key=lambda x: -x[1])[:2]
            top_str = "  ".join(f"{k}:{v:.2f}" for k, v in top2)
            mark = " " if rule == ml_pred else "✗"
            dt_str = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
            print(f"{dt_str:>20s}  {c:>9.2f}  {rule:>12s}  {ml_pred:>12s}{mark} {top_str}")


if __name__ == "__main__":
    main()
