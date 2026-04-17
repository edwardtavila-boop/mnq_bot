# Journal Replay Audit

- Journal: `/sessions/kind-keen-faraday/data/live_sim/journal.sqlite`
- Deterministic two-pass replay: **OK**
- Total events: **669**
- Event checksum (SHA-256): `1a7a4b7305c9d01b7936dce73a069f1015a6d81b52f8ee59a35fcde19170b7e2`
- Reconstructed orders: **74**

## Event-type distribution

| Event type | Count |
|---|---:|
| `drift.ok` | 1 |
| `fill.expected` | 74 |
| `fill.realized` | 111 |
| `order.acked` | 74 |
| `order.filled` | 74 |
| `order.submitted` | 74 |
| `order.working` | 74 |
| `pnl.update` | 37 |
| `position.update` | 37 |
| `reconcile.ok` | 1 |
| `reconcile.start` | 1 |
| `safety.decision` | 111 |

## Reconstructed positions

| Symbol | Net position |
|---|---:|
| MNQ | 0 |

## Interpretation

* A stable two-pass checksum is the prerequisite for byte-for-byte parity with a future live shadow stream.
* Non-flat positions after a complete day's replay indicate a ghost position — investigate the reconciler output immediately.
* If this checksum changes without a schema migration, either an event was rewritten in place (journal breach) or the event stream was re-ordered — both are incidents.
