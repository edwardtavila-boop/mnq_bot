# Firm Integration — Readiness Probe

- Firm code path: `C:\EvolutionaryTradingAlgo\firm`
- Path exists: **True**
- Ready for integration: **False**

## Module probe

| Module | Importable | Required names resolved |
|---|---|---|
| `firm.types` | no | 0/2 |
| `firm.agents.base` | no | 0/3 |
| `firm.agents.core` | no | 0/6 |

## Gaps blocking integration

- `firm.types.Verdict`
- `firm.types.Quadrant`
- `firm.agents.base.Agent`
- `firm.agents.base.AgentInput`
- `firm.agents.base.AgentOutput`
- `firm.agents.core.QuantAgent`
- `firm.agents.core.RedTeamAgent`
- `firm.agents.core.RiskManagerAgent`
- `firm.agents.core.MacroAgent`
- `firm.agents.core.MicrostructureAgent`
- `firm.agents.core.PMAgent`
- `firm.agents.core.signature: firm.agents.core import failed: No module named 'firm.agents'`

## Next step

Continue running the markdown-only Firm review path (`scripts/firm_review.py`). Rerun this probe after each Firm-code fine-tune cycle; integration will auto-enable when the contract is met.
