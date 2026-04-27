"""Level 6 — chaos tests.

The unit (L1), property (L2), parity (L3), replay (L4) and integration (L5)
levels cover the happy path. L6 proves the system also survives:

- kill-9 mid-fill (reconciler recovers from journal)
- heartbeat gaps (deadman trips at the expected age)
- clock skew (feature staleness gate fires before a stale trade ships)
- partial ack loss (submit without ack without fill still resolves cleanly)
- journal corruption (malformed rows are skipped, not crashed on)

These tests are intentionally paranoid. They are the cheapest way to flush
out "works in prod until it doesn't" bugs before a live session exposes
them. Each test is self-contained — no network, no external processes — so
they are safe for CI.
"""
