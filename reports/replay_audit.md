# Journal Replay Audit

- Journal: `C:\Users\edwar\projects\mnq_bot\data\live_sim\journal.sqlite`
- Deterministic two-pass replay: **OK**
- Total events: **669**
- Event checksum (SHA-256): `9486cf755dd0bcb4a3714284f49544519227a72f05b6ffa7a6d52fa49c3ed62c`
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
