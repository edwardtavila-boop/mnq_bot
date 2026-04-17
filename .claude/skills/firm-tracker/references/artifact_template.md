# Artifact Template

The canonical React artifact template is at `references/artifact_template.jsx` in this skill directory.

When regenerating the dashboard:

1. Read the template file
2. Replace the data constants at the top (SYSTEM, SIM, TODAY, CALIBRATION, WALKFORWARD, FIRM_FILTER, BAYESIAN, REGIME_PNL, VERDICT, STAGES, PHASES) with fresh values extracted from the report files
3. Do NOT change the component logic, styling, or layout unless the user specifically requests it
4. Save the updated file to the user's workspace as `firm_command_center.jsx`

The data constants section spans roughly lines 10-170 of the template. Everything below that is the rendering code which should be preserved as-is.

## Key data constants to update

- `SYSTEM` — lastRun, totalDuration, testsPass, firmReady, firmContract, registryVariants
- `SIM` — all fields from live_sim_analysis.md
- `TODAY` — from daily/<date>.md
- `CALIBRATION` — from calibration.md
- `WALKFORWARD` — from walk_forward.md
- `FIRM_FILTER` — from firm_vs_baseline.md
- `BAYESIAN` — array from bayesian_expectancy.md
- `REGIME_PNL` — from live_sim_analysis.md per-regime table
- `VERDICT` — from firm_reviews/<variant>_live.md
- `STAGES` — from run_all_phases.md stage ledger
- `PHASES` — task completion from roadmap_tasks.md verification rules
