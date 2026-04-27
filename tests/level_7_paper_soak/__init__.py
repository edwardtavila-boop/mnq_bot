"""Level 7 — paper soak.

Long-running replays against real historical tape (Databento MNQ 1m cache)
with chaos events interleaved. L6 proved each individual failure mode is
survivable in isolation; L7 proves the *combination* of failure modes
doesn't compound into a crash over thousands of bars.

These tests are slower than L1-L6 (still sub-second per test, but with
noticeably more I/O) and intentionally use real data so the shape of the
inputs matches live conditions.
"""
