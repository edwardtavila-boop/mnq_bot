# Burn-in Harness — 2026-04-26T14:26:26.882614+00:00

**Simulated:** 72h  ·  **Wall time:** 4.2s  ·  **Compression:** 4800.0×
**Verdict:** 🟢 ALL CHECKS GREEN

## Checks

| Check | Result | Observed | Threshold |
|---|---|---|---|
| Events emitted (≥ 1/s) | 🟢 | 263,519 | ≥ 259,200 |
| Sequence monotonic (no gaps) | 🟢 | yes | yes |
| Deterministic checksum | 🟢 | d16aef7e4005570d | stable across reads |
| WAL mode preserved | 🟢 | wal | wal |
| Max heartbeat age | 🟢 | 1.00s | < 5s |
| Memory drift | 🟢 | +0.0% | < 25% |

## Memory envelope

- Start: 0 KiB
- End:   0 KiB
- Drift: +0.00%

## Notes

- Burn-in writes to `data/burn_in/journal.sqlite` (isolated from live_sim).
- Default run compresses 72h → ~54s wall. Pass `--compression 1` for true realtime.
- This harness doesn't prove trading correctness — it proves the event-sourcing
  spine doesn't crash, leak, or drift under sustained load.
