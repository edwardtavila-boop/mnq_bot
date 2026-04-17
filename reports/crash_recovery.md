# Crash Recovery Test

- events recovered: **200** / 200
- errors: **0**
- OK: **True**

## Procedure

1. Spawn subprocess that writes n events then `os._exit(137)`.
2. Reopen the journal in a fresh process.
3. Replay all events and assert (seq, type, payload) match.

WAL mode + `PRAGMA synchronous=FULL` should guarantee every committed event is recovered exactly.
