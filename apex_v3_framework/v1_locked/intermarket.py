"""
Intermarket Data Loader
=======================
Loads NQ + sibling feeds (VIX, ES, DXY, TICK) and merges them by timestamp.
Each output bar carries optional sibling data — None when sibling has no
matching timestamp (handles ragged-edge data gracefully).

Usage:
    bars = load_with_intermarket("mnq_5m.csv", vix="mnq_vix_5.csv",
                                  es="mnq_es1_5.csv", dxy="mnq_dxy_5.csv",
                                  tick="mnq_tick_5.csv")
    for bar in bars:
        bar.vix_close       # or None
        bar.es_close        # or None
        bar.dxy_close       # or None
        bar.tick_close      # or None
"""

import csv
from typing import List, Optional, Dict
from firm_engine import Bar


def _load_simple(path: str) -> Dict[int, Dict[str, float]]:
    """Load a CSV indexed by epoch_s. Returns {time: {open,high,low,close,vol}}."""
    out = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = int(row.get('time') or row['epoch_s'])
                out[t] = {
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                }
            except (KeyError, ValueError):
                continue
    return out


def load_with_intermarket(main_path: str,
                          vix: Optional[str] = None,
                          es: Optional[str] = None,
                          dxy: Optional[str] = None,
                          tick: Optional[str] = None) -> List[Bar]:
    """Load main NQ data and attach intermarket siblings by timestamp."""
    bars = []
    vix_data = _load_simple(vix) if vix else {}
    es_data = _load_simple(es) if es else {}
    dxy_data = _load_simple(dxy) if dxy else {}
    tick_data = _load_simple(tick) if tick else {}

    with open(main_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = int(float(row.get('time') or row['epoch_s']))
                bar = Bar(
                    time=t,
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=float(row['volume']) if row['volume'].replace('.','').replace('-','').isdigit() else 0,
                )
                # Attach intermarket data as new attributes
                v = vix_data.get(t)
                bar.vix_close = v['close'] if v else None
                bar.vix_high = v['high'] if v else None
                bar.vix_open = v['open'] if v else None
                e = es_data.get(t)
                bar.es_close = e['close'] if e else None
                bar.es_open = e['open'] if e else None
                d = dxy_data.get(t)
                bar.dxy_close = d['close'] if d else None
                bar.dxy_open = d['open'] if d else None
                tk = tick_data.get(t)
                bar.tick_close = tk['close'] if tk else None
                bars.append(bar)
            except (KeyError, ValueError):
                continue
    return bars


def coverage_report(bars: List[Bar]) -> dict:
    """Show how many bars have each intermarket feed."""
    n = len(bars)
    return {
        "total_bars": n,
        "with_vix": sum(1 for b in bars if getattr(b, 'vix_close', None) is not None),
        "with_es": sum(1 for b in bars if getattr(b, 'es_close', None) is not None),
        "with_dxy": sum(1 for b in bars if getattr(b, 'dxy_close', None) is not None),
        "with_tick": sum(1 for b in bars if getattr(b, 'tick_close', None) is not None),
    }
