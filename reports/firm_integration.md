# Firm Integration — Readiness Probe

- Firm code path: `C:\Users\edwar\projects\firm`
- Path exists: **True**
- Ready for integration: **True**

## Module probe

| Module | Importable | Required names resolved |
|---|---|---|
| `firm.types` | yes | 2/2 |
| `firm.agents.base` | yes | 3/3 |
| `firm.agents.core` | yes | 6/6 |

## Gaps blocking integration

_none — contract satisfied._

## Next step

Run `python scripts/firm_bridge.py --integrate` to emit the runtime shim at `src/mnq/firm_runtime.py`. Live_sim will then delegate the six-stage review to the real Firm agents.
