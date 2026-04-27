import { useState, useMemo } from "react";

// ═══════════════════════════════════════════════════════════════════
// THE FIRM — COMMAND CENTER
// Elite MNQ automation dashboard. Six tabs. Live data. No fluff.
// ═══════════════════════════════════════════════════════════════════

// ── Live data from reports (2026-04-15) ──────────────────────────

const SYSTEM = {
  lastRun: "2026-04-15 11:40:29 UTC",
  totalDuration: "20.3s",
  pythonVersion: "3.14.3",
  testsPass: 534,
  testsSkip: 2,
  firmReady: true,
  firmContract: { types: "2/2", base: "3/3", agents: "6/6" },
  registryVariants: 41,
};

const SIM = {
  days: 20, bars: 7800, signals: 37, fills: 37, roundTrips: 37,
  blockedByRisk: 0, breakerHalts: 0, malformed: 74,
  netPnl: 101.62, expectancy: 2.75, winRate: 70.3,
  avgSlippage: 1.072, medianSlippage: 1.0, p95Slippage: 2.0,
  turnoverMu: 2.08, turnoverRealized: 1.85, turnoverZ: -1.15,
  reconcileDiffs: 0,
};

const TODAY = {
  date: "2026-04-15", closedTrades: 8, grossPnl: 7.08,
  winRate: 37.5, biggestWin: 38.76, biggestLoss: -21.74,
  meanSlippage: 1.0, totalEvents: 147,
};

const CALIBRATION = {
  n: 37, baseRate: 0.703,
  brierIS: 0.0876, logLossIS: 0.2959,
  brierLOOCV: 0.1495, logLossLOOCV: 0.4533,
  buckets: [
    { pred: 0.11, realized: 0.0, n: 4 },
    { pred: 0.285, realized: 0.333, n: 3 },
    { pred: 0.499, realized: 0.333, n: 6 },
    { pred: 0.724, realized: 0.833, n: 6 },
    { pred: 0.911, realized: 1.0, n: 18 },
  ],
};

const WALKFORWARD = {
  trainWindow: 8, testWindow: 3, stride: 1, folds: 5,
  totalTestPnl: 36.0, totalTestTrades: 7, meanPnlPerFold: 7.2,
  stdevPnlPerFold: 27.24, positiveFolds: 3,
  winnerStability: "5/5",
  folds: [
    { id: 0, trainPnl: 14.5, testPnl: 18.0, testN: 2, testWR: 50 },
    { id: 1, trainPnl: -26.5, testPnl: 40.5, testN: 1, testWR: 100 },
    { id: 2, trainPnl: -26.5, testPnl: 19.5, testN: 2, testWR: 50 },
    { id: 3, trainPnl: 36.5, testPnl: -21.0, testN: 1, testWR: 0 },
    { id: 4, trainPnl: 36.5, testPnl: -21.0, testN: 1, testWR: 0 },
  ],
};

const FIRM_FILTER = {
  filteredTrades: 8, baselineTrades: 47,
  filteredPnl: 11.5, baselinePnl: -138.0, lift: 149.5,
  filteredWR: 37.5, baselineWR: 27.7,
  filteredExp: 1.44, baselineExp: -2.94,
  ciLow: 23.5, ciHigh: 269.5,
};

const BAYESIAN = [
  { regime: "trend_down", side: "long", n: 4, wr: 83.3, ciLo: 48.0, ciHi: 99.5, postExp: 6.26, expLo: 3.6, heat: 1 },
  { regime: "trend_up", side: "short", n: 3, wr: 80.0, ciLo: 39.8, ciHi: 99.2, postExp: 6.07, expLo: 3.02, heat: 1 },
  { regime: "trend_down", side: "short", n: 3, wr: 80.0, ciLo: 39.8, ciHi: 99.2, postExp: 5.94, expLo: 2.95, heat: 1 },
  { regime: "trend_up", side: "long", n: 2, wr: 75.0, ciLo: 29.2, ciHi: 99.2, postExp: 5.45, expLo: 2.12, heat: 1 },
  { regime: "range_bound", side: "long", n: 1, wr: 66.7, ciLo: 16.0, ciHi: 98.8, postExp: 4.84, expLo: 1.16, heat: 1 },
  { regime: "chop", side: "long", n: 8, wr: 70.0, ciLo: 40.0, ciHi: 92.8, postExp: 2.66, expLo: -1.69, heat: 0 },
  { regime: "chop", side: "short", n: 5, wr: 71.4, ciLo: 36.0, ciHi: 95.8, postExp: 2.94, expLo: -2.11, heat: 0 },
  { regime: "high_vol", side: "long", n: 8, wr: 30.0, ciLo: 7.5, ciHi: 60.2, postExp: -3.41, expLo: -6.59, heat: 0 },
  { regime: "high_vol", side: "short", n: 3, wr: 40.0, ciLo: 7.0, ciHi: 80.8, postExp: -2.39, expLo: -7.42, heat: 0 },
];

const REGIME_PNL = [
  { regime: "chop", trades: 13, wins: 10, wr: 76.9, pnl: 47.88, slip: 1.23 },
  { regime: "high_vol", trades: 11, wins: 3, wr: 27.3, pnl: -43.14, slip: 1.27 },
  { regime: "range_bound", trades: 1, wins: 1, wr: 100, pnl: 7.26, slip: 1.0 },
  { regime: "trend_down", trades: 7, wins: 7, wr: 100, pnl: 52.32, slip: 0.86 },
  { regime: "trend_up", trades: 5, wins: 5, wr: 100, pnl: 37.30, slip: 0.80 },
];

const VERDICT = {
  strategy: "r5_real_wide_target",
  specPayload: {
    sample_size: 8, expected_expectancy_r: 0.072, oos_degradation_pct: 120.51,
    entry_logic: "EMA9/EMA21 cross, min spread 2.00 pts, vol filter sigma<=17.0, hard pause sigma>28.0, orderflow proxy>=0.60",
    stop_logic: "40-tick hard stop; time stop 20 bars",
    target_logic: "2.0R fixed target", dd_kill_switch_r: 12.0,
    regimes_approved: ["real_trend_up"],
  },
  stages: [
    { name: "Quant", role: "Setup Author", verdict: "MODIFY", prob: 0.20, ci: [0.05, 0.35],
      reasoning: "Sample size 8 < 100 floor; OOS degradation 120.5% > 50% threshold; expectancy 0.072R marginal",
      falsification: "Live expectancy < 0.036R across first 50 trades OR OOS degradation > 140.5%",
      attacks: ["Sample size 8 < 100 minimum", "OOS degradation 120.51% > 50% threshold"] },
    { name: "Red Team", role: "Attacker", verdict: "KILL", prob: 0.90, ci: [0.70, 1.0],
      reasoning: "4 critical attack surfaces identified — mandatory rejection",
      falsification: "First 100 live trades show no instances of attacked failure modes",
      attacks: ["Overfitting: OOS degradation 120.51% suggests curve-fit", "Sample size: N=8, bootstrap CI lower < 0.20R", "Regime fragility: 0 approved regimes", "Execution: slippage not modeled"] },
    { name: "Risk", role: "Gatekeeper", verdict: "HOLD", prob: 0.70, ci: [0.55, 0.85],
      reasoning: "Kelly fraction = 0.000 — edge too small to overcome costs",
      falsification: "Realized DD > -12R kill level OR live edge < 50% of backtest after 50 trades",
      attacks: ["Kelly = 0.000", "Per-trade risk: 0.001%", "DD kill: -12.0R"] },
    { name: "Macro", role: "Regime Context", verdict: "GO", prob: 0.50, ci: [0.35, 0.65],
      reasoning: "No major catalysts pending; regime match acceptable",
      falsification: "Regime revised within 24h OR major catalyst surprise",
      attacks: [] },
    { name: "Micro", role: "Execution Reality", verdict: "GO", prob: 0.60, ci: [0.45, 0.75],
      reasoning: "Spread 1.0t, latency 200ms, edge cost 0% — executable",
      falsification: "Realized slippage > 1.5t OR latency > 500ms budget",
      attacks: [] },
    { name: "PM", role: "Final Decision", verdict: "KILL", prob: 0.90, ci: [0.80, 1.0],
      reasoning: "Red Team filed mandatory dissent with 4 critical attacks; 1 KILL verdict triggers automatic rejection",
      falsification: "N/A — aggregate decision",
      attacks: ["Red Team KILL vetoes entire pipeline"] },
  ],
};

// ── JARVIS (Financial Jarvis — principles loop) ──────────────────
// Populated from:
//   docs/premarket_latest.json           (scripts.daily_premarket)
//   docs/weekly_checklist_latest.json    (scripts.weekly_review --checklist-answers)
//   docs/monthly_review_latest.json      (scripts.monthly_deep_review)
// Update these constants when regenerating the dashboard; the structure
// mirrors JarvisContext + ChecklistReport + monthly_deep_review.run().

const JARVIS_PRINCIPLES = [
  { idx: 0, slug: "a_plus_only", q: "Did I pass on B-grade setups?" },
  { idx: 1, slug: "process_over_outcome", q: "Did I follow my checklist on every trade?" },
  { idx: 2, slug: "decision_log", q: "Did every trade get a journaled rationale?" },
  { idx: 3, slug: "consult_jarvis", q: "Did I consult the Jarvis snapshot before entries?" },
  { idx: 4, slug: "never_autopilot", q: "Did I ack all watchdog prompts in time?" },
  { idx: 5, slug: "cadence_of_review", q: "Did I run the weekly review on schedule?" },
  { idx: 6, slug: "stress_testing", q: "Did I stress-test before any size/parameter change?" },
  { idx: 7, slug: "risk_discipline", q: "Did I stay under the daily DD limit?" },
  { idx: 8, slug: "override_discipline", q: "Did I keep override_rate <= 10%?" },
  { idx: 9, slug: "continuous_learning", q: "Did I extract a written lesson from every loser?" },
];

const JARVIS = {
  premarket: {
    ts: "2026-04-17T16:25:28Z",
    action: "TRADE",               // TRADE / STAND_ASIDE / REDUCE / REVIEW / KILL
    reason: "all gates green",
    confidence: 0.80,
    warnings: [],
    // v2: session awareness
    sessionPhase: "LUNCH",         // OVERNIGHT / PREMARKET / OPEN_DRIVE / MORNING / LUNCH / AFTERNOON / CLOSE
    // v2: stress score
    stressScore: {
      composite: 0.0,
      bindingConstraint: "macro_event",
      components: [
        { name: "macro_event",   value: 0.00, weight: 0.25, contribution: 0.000, note: "no event" },
        { name: "equity_dd",     value: 0.00, weight: 0.25, contribution: 0.000, note: "dd 0.00%" },
        { name: "open_risk",     value: 0.00, weight: 0.15, contribution: 0.000, note: "0.00R" },
        { name: "regime_risk",   value: 0.00, weight: 0.10, contribution: 0.000, note: "UNKNOWN" },
        { name: "override_rate", value: 0.00, weight: 0.10, contribution: 0.000, note: "0 in 24h" },
        { name: "autopilot",     value: 0.00, weight: 0.07, contribution: 0.000, note: "ACTIVE" },
        { name: "correlations",  value: 0.00, weight: 0.05, contribution: 0.000, note: "clear" },
        { name: "macro_bias",    value: 0.00, weight: 0.03, contribution: 0.000, note: "neutral" },
      ],
    },
    // v2: sizing hint
    sizingHint: {
      sizeMult: 0.70,
      reason: "stress low -- full size authorized; session=LUNCH x0.70",
      kellyCap: null,
    },
    // v2: alerts (sorted by severity descending)
    alerts: [],   // [{level, code, message, severity}]
    // v2: margins to next action tier
    margins: {
      ddToReduce:       0.020,   // +2.00%  headroom
      ddToStandAside:   0.030,
      ddToKill:         0.050,
      overridesToReview: 3,
      openRiskToCapR:   3.00,
    },
    // v2: trajectory (present only after engine has >=3 ticks)
    trajectory: null,   // {dd, stress, overridesVelocity24h, samples, windowSeconds}
    // v2: concrete step list
    playbook: [
      "take only A+ setups that pass the full checklist",
      "size per Kelly cap + sizing_hint.size_mult",
      "journal decision rationale before entry",
      "binding constraint: macro_event",
      "lunch chop: fade only, no trend continuation trades",
    ],
    // v2: natural-language one-paragraph summary
    explanation: "Jarvis says TRADE (confidence 80%) because all gates green. " +
                 "Composite stress is 0%, dominated by macro_event. " +
                 "DD headroom: 2.00% before REDUCE, 3.00% before STAND_ASIDE. " +
                 "Session LUNCH. Suggested size 70%.",
    macro: {
      vix: null, bias: "neutral",
      nextEvent: null, hoursToEvent: null,
    },
    regime: {
      current: "UNKNOWN", confidence: 0.50,
      previous: null, flipped: false,
    },
    equity: {
      equity: 0, pnlToday: 0, ddToday: 0.0,
      positions: 0, openRiskR: 0.0,
    },
    journal: {
      killSwitch: false, mode: "ACTIVE",
      executed24h: 0, blocked24h: 0, overrides24h: 0,
      corrAlert: false,
    },
    notes: ["no premarket_inputs.json found -- stub snapshot"],
  },
  checklist: {
    period: "2026-W16",
    ts: "2026-04-17T15:53:39Z",
    score: 0.80, letter: "B", discipline: 8,
    // index -> yes (true/false)
    answers: {0: true, 1: true, 2: true, 3: true, 4: true, 5: true,
              6: false, 7: true, 8: true, 9: false},
    criticalGaps: ["stress_testing", "continuous_learning"],
  },
  monthly: {
    period: "2026_04",
    generatedAt: "2026-04-17T15:53:28Z",
    gradingN: 0, meanTotal: null, distribution: {},
    exitQualityN: 0,
    rationalesN: 0,
    proposedTweaks: [],   // [{category, rationale, metric, severity}]
  },
  // summary derived by UI: bundle modules shipped
  bundle: {
    moduleCount: 11,
    moduleNames: [
      "core/trade_grader",
      "obs/decision_journal",
      "brain/jarvis_context  (v2 -- stress/session/sizing/alerts/memory/engine)",
      "brain/jarvis_admin    (v0.1.29 -- chain-of-command authority, 46 tests)",
      "core/principles_checklist",
      "scripts/daily_premarket",
      "scripts/monthly_deep_review",
      "obs/gate_override_telemetry",
      "obs/autopilot_watchdog  (admin-wired, 21 tests)",
      "brain/rationale_miner",
      "backtest/exit_quality",
    ],
    testCount: 1385,  // full eta_engine suite through v0.1.29 CC integration
    shippedVersion: "v0.1.29",
  },
};

// ---------------------------------------------------------------------------
// JARVIS ADMIN -- chain-of-command state (v0.1.29)
// "Everyone reports to Jarvis" -- every autonomous subsystem must request
// approval from JarvisAdmin before taking any action. This constant
// describes the org-chart and a tail of recent approval decisions.
// ---------------------------------------------------------------------------
const JARVIS_ADMIN = {
  version: "v0.1.29",
  // Subsystems grouped by scope; every one reports to Jarvis for approval.
  commandTree: [
    {
      group: "Bot Fleet (eta_engine)",
      items: [
        { id: "bot.crypto_seed", label: "crypto_seed",    mode: "ACTIVE",    tier: "TRADE" },
        { id: "bot.eth_perp",    label: "eth_perp",       mode: "ACTIVE",    tier: "TRADE" },
        { id: "bot.mnq",         label: "mnq",            mode: "ACTIVE",    tier: "TRADE" },
        { id: "bot.nq",          label: "nq",             mode: "ACTIVE",    tier: "TRADE" },
      ],
    },
    {
      group: "Framework (mnq_bot v3)",
      items: [
        { id: "framework.autopilot",        label: "autopilot",        mode: "ACTIVE",  tier: "TRADE" },
        { id: "framework.firm_engine",      label: "firm_engine",      mode: "ACTIVE",  tier: "TRADE" },
        { id: "framework.court_of_appeals", label: "court_of_appeals", mode: "ACTIVE",  tier: "TRADE" },
        { id: "framework.confluence_scorer",label: "confluence",       mode: "ACTIVE",  tier: "TRADE" },
        { id: "framework.webhook",          label: "webhook",          mode: "ACTIVE",  tier: "TRADE" },
        { id: "framework.meta_orchestrator",label: "meta_orch",        mode: "ACTIVE",  tier: "TRADE" },
      ],
    },
    {
      group: "The Firm (6 agents)",
      items: [
        { id: "firm.quant",    label: "quant",    mode: "ACTIVE", tier: "TRADE" },
        { id: "firm.red_team", label: "red_team", mode: "ACTIVE", tier: "TRADE" },
        { id: "firm.risk",     label: "risk",     mode: "ACTIVE", tier: "TRADE" },
        { id: "firm.macro",    label: "macro",    mode: "ACTIVE", tier: "TRADE" },
        { id: "firm.micro",    label: "micro",    mode: "ACTIVE", tier: "TRADE" },
        { id: "firm.pm",       label: "pm",       mode: "ACTIVE", tier: "TRADE" },
      ],
    },
    {
      group: "Guards & Operator",
      items: [
        { id: "gates.chain",         label: "gate_chain",         mode: "ACTIVE", tier: "TRADE" },
        { id: "watchdog.autopilot",  label: "autopilot_watchdog", mode: "ACTIVE", tier: "TRADE" },
        { id: "operator.edward",     label: "operator",           mode: "ACTIVE", tier: "TRADE" },
      ],
    },
  ],
  // Rolling tail of the audit log (most recent first).
  // Each entry: { ts, subsystem, action, verdict, reason_code, reason, size_cap_mult }
  recentAudit: [
    // Seeded with one POC entry so the panel is never empty.
    {
      ts: "2026-04-17T16:25:29Z",
      subsystem: "watchdog.autopilot",
      action: "POSITION_FLATTEN",
      verdict: "APPROVED",
      reason_code: "kill_exit_permitted",
      reason: "exit-only action permitted (admin v0.1.29 CC POC)",
      size_cap_mult: null,
    },
  ],
};

// ── Integration topology (from eta_engine/docs/integrations_latest.json) ──
// Schema + canonical data are defined in eta_engine.funnel.integrations.
// Consumed by <IntegrationsTab/> below. Mirror of the JSON emitted by
// `python -m eta_engine.scripts.build_integrations_report`.
const INTEGRATIONS = {
  schemaVersion: "1.0",
  venues: [
    { name: "tradovate",   kind: "futures",     assets: "MNQ, NQ",                      status: "NEEDS_FUNDING",
      notes: "OAuth2 blocked on $1000 funded balance." },
    { name: "bybit",       kind: "perps_cex",   assets: "ETH-PERP, SOL-PERP, XRP-PERP", status: "READY",          notes: "" },
    { name: "okx",         kind: "spot_cex",    assets: "spot",                         status: "READY",          notes: "" },
    { name: "coinbase",    kind: "onramp",      assets: "BTC, ETH, USDC",               status: "READY",          notes: "ACH onramp." },
    { name: "kraken",      kind: "onramp",      assets: "USDC, USDT",                   status: "READY",          notes: "Bank-wire onramp." },
    { name: "ledger_cold", kind: "cold_wallet", assets: "BTC, ETH, SOL, stables",       status: "READY",          notes: "Air-gapped custody." },
  ],
  bots: [
    { name: "mnq",         venue: "tradovate", layer: "LAYER_1_MNQ",   tier: "A",      status: "PAPER", notes: "168 trades +0.473R; live-tiny blocked on funding." },
    { name: "nq",          venue: "tradovate", layer: "LAYER_1_MNQ",   tier: "A",      status: "PAPER", notes: "140 trades +0.607R; $20 point value." },
    { name: "crypto_seed", venue: "bybit",     layer: "LAYER_2_BTC",   tier: "SEED",   status: "PAPER", notes: "161 trades +0.149R; gate FAIL pending real bars." },
    { name: "eth_perp",    venue: "bybit",     layer: "LAYER_3_PERPS", tier: "CASINO", status: "PAPER", notes: "paper +0.161R; gate FAIL pending real bars." },
    { name: "sol_perp",    venue: "bybit",     layer: "LAYER_3_PERPS", tier: "CASINO", status: "PAPER", notes: "paper +0.146R." },
    { name: "xrp_perp",    venue: "bybit",     layer: "LAYER_3_PERPS", tier: "CASINO", status: "PAPER", notes: "paper +0.176R; 15.55% DD." },
  ],
  funnelLayers: [
    { id: "LAYER_1_MNQ",     label: "MNQ futures compounder",          sweep: 0.50, killDD: 0.08, lev: 10.0, tier: "A",      notes: "60% of profit stack." },
    { id: "LAYER_2_BTC",     label: "BTC spot / grid seed",            sweep: 0.40, killDD: 0.10, lev: 3.0,  tier: "B",      notes: "10% of stack." },
    { id: "LAYER_3_PERPS",   label: "ETH/SOL/XRP perps (casino tier)", sweep: 0.30, killDD: 0.15, lev: 5.0,  tier: "CASINO", notes: "30% of stack." },
    { id: "LAYER_4_STAKING", label: "Staking compound (terminal)",     sweep: 0.00, killDD: 1.00, lev: 1.0,  tier: "SINK",   notes: "Terminal; no outflow." },
  ],
  onrampRoutes: [
    { fiat: "ACH",       provider: "COINBASE", crypto: "BTC",  perTxn: 10000, monthly: 50000 },
    { fiat: "ACH",       provider: "COINBASE", crypto: "ETH",  perTxn: 10000, monthly: 50000 },
    { fiat: "BANK_WIRE", provider: "KRAKEN",   crypto: "USDC", perTxn: 10000, monthly: 50000 },
  ],
  staking: [
    { protocol: "Lido",   chain: "ethereum", assetIn: "ETH",  assetOut: "wstETH",  apy: 3.2,  notes: "Optional EigenLayer +1.5%." },
    { protocol: "Jito",   chain: "solana",   assetIn: "SOL",  assetOut: "JitoSOL", apy: 7.5,  notes: "" },
    { protocol: "Flare",  chain: "flare",    assetIn: "FLR",  assetOut: "sFLR",    apy: 4.1,  notes: "FTSO delegation for +reward." },
    { protocol: "Ethena", chain: "ethereum", assetIn: "USDT", assetOut: "sUSDe",   apy: 12.0, notes: "7-day unstake cooldown." },
  ],
  observability: [
    { name: "JarvisSupervisor",      kind: "supervisor", status: "ACTIVE",  notes: "60s cadence via scripts.jarvis_live." },
    { name: "TelegramAlerter",       kind: "alerter",    status: "DRY_RUN", notes: "Needs TELEGRAM_BOT_TOKEN+CHAT_ID." },
    { name: "DiscordAlerter",        kind: "alerter",    status: "DRY_RUN", notes: "Needs DISCORD_WEBHOOK_URL." },
    { name: "SlackAlerter",          kind: "alerter",    status: "DRY_RUN", notes: "Needs SLACK_WEBHOOK_URL." },
    { name: "GateOverrideTelemetry", kind: "telemetry",  status: "ACTIVE",  notes: "Prometheus counters." },
    { name: "DecisionJournal",       kind: "journal",    status: "ACTIVE",  notes: "Append-only JSONL." },
    { name: "AutopilotWatchdog",     kind: "supervisor", status: "ACTIVE",  notes: "REQUIRE_ACK → TIGHTEN_STOP → FORCE_FLATTEN." },
  ],
};

const STAGES = [
  { n: 1, phase: "Phase 0", name: "firm_bridge", dur: 0.1, status: "OK" },
  { n: 2, phase: "Phase 0", name: "live_sim", dur: 4.4, status: "OK" },
  { n: 3, phase: "Phase 2", name: "replay_journal", dur: 1.6, status: "OK" },
  { n: 4, phase: "Phase 1", name: "crash_recovery", dur: 0.4, status: "OK" },
  { n: 5, phase: "Cross", name: "strategy_registry", dur: 1.3, status: "OK" },
  { n: 6, phase: "Phase 3", name: "strategy_ab", dur: 2.4, status: "OK" },
  { n: 7, phase: "Cross", name: "walk_forward", dur: 2.3, status: "OK" },
  { n: 8, phase: "Phase 3", name: "firm_vs_baseline", dur: 2.2, status: "OK" },
  { n: 9, phase: "Phase 3", name: "calibration", dur: 1.1, status: "OK" },
  { n: 10, phase: "Phase 5", name: "bayesian_expectancy", dur: 0.1, status: "OK" },
  { n: 11, phase: "Phase 3", name: "firm_review_markdown", dur: 2.1, status: "OK" },
  { n: 12, phase: "Phase 3", name: "firm_live_review", dur: 2.1, status: "OK" },
  { n: 13, phase: "Phase 3", name: "postmortem", dur: 0.2, status: "OK" },
  { n: 14, phase: "Phase 1", name: "daily_digest", dur: 0.1, status: "OK" },
];

const PHASES = [
  { id: 0, title: "Verify Integration", status: "complete", pct: 95,
    desc: "Sim boots → signals → fills → journal. Firm bridge probes + integrates.",
    tasks: [
      { t: "Sim boots, emits signals, fills, journals", d: true },
      { t: "Firm skill mounted + plugin loaded", d: true },
      { t: "Firm bridge probe (CONTRACT validated)", d: true },
      { t: "Runtime shim auto-generated", d: true },
      { t: "live_sim end-to-end pass", d: true },
      { t: "Watchdog / heartbeat wiring", d: false },
    ],
    stageNames: ["firm_bridge", "live_sim"],
  },
  { id: 1, title: "Harden Foundation", status: "active", pct: 70,
    desc: "Crash recovery, daily digest, structured logging, 72h burn-in.",
    tasks: [
      { t: "Structured logging + WAL SQLite journal", d: true },
      { t: "Crash recovery test (250/250 events)", d: true },
      { t: "Daily digest generation", d: true },
      { t: "Strategy graveyard + bug journal", d: true },
      { t: "72h unattended burn-in", d: false },
    ],
    stageNames: ["crash_recovery", "daily_digest"],
  },
  { id: 2, title: "Event Log & Replay", status: "complete", pct: 100,
    desc: "Event-sourced SQLite journal with deterministic replay verification.",
    tasks: [
      { t: "SQLite journal with typed events", d: true },
      { t: "Determinism replay harness", d: true },
      { t: "Backtest/live parity checksum", d: true },
    ],
    stageNames: ["replay_journal"],
  },
  { id: 3, title: "Fill Documented Gaps", status: "active", pct: 90,
    desc: "Calibration, Firm reviews (markdown + live agents), post-mortems, A/B.",
    tasks: [
      { t: "Calibration (Brier / log-loss / LOOCV)", d: true },
      { t: "Firm-vs-baseline backtest", d: true },
      { t: "Auto post-mortem generation", d: true },
      { t: "Firm review — markdown path", d: true },
      { t: "Firm review — LIVE Python agents", d: true },
      { t: "Strategy A/B harness", d: true },
      { t: "Pre-mortem template wiring", d: true },
      { t: "Gauntlet gate implementation (12 gates)", d: false },
    ],
    stageNames: ["strategy_ab", "firm_vs_baseline", "calibration", "firm_review_markdown", "firm_live_review", "postmortem"],
  },
  { id: 4, title: "Backtest / Live Parity", status: "active", pct: 60,
    desc: "Paper-sim vs FILL_REALIZED shadow stream within tolerance.",
    tasks: [
      { t: "summarize_env parity shape", d: true },
      { t: "Replay journal determinism assertions", d: true },
      { t: "Tolerance harness (paper vs shadow)", d: false },
    ],
    stageNames: [],
  },
  { id: 5, title: "Advanced Risk", status: "active", pct: 75,
    desc: "Kelly sizing, Bayesian expectancy, heat/concurrency budgets.",
    tasks: [
      { t: "Kelly fraction with shrinkage", d: true },
      { t: "Bayesian expectancy (Beta posteriors)", d: true },
      { t: "Heat / concurrency budget per regime", d: true },
      { t: "Full risk manager integration", d: false },
    ],
    stageNames: ["bayesian_expectancy"],
  },
  { id: 6, title: "API Boundary / VPS", status: "blocked", pct: 0,
    desc: "Deploy to VPS with API boundary. Requires external infrastructure.",
    tasks: [
      { t: "VPS provisioning", d: false },
      { t: "Docker deployment", d: false },
      { t: "API boundary / auth", d: false },
      { t: "Chicago VPN for low-latency", d: false },
    ],
    stageNames: [],
  },
  { id: 7, title: "Real Broker", status: "blocked", pct: 0,
    desc: "Connect to Tradovate live API.",
    tasks: [
      { t: "Tradovate REST client (exists)", d: true },
      { t: "Tradovate WebSocket client (exists)", d: true },
      { t: "Auth token lifecycle (exists)", d: true },
      { t: "Live order routing", d: false },
      { t: "Position reconciliation", d: false },
    ],
    stageNames: [],
  },
  { id: 8, title: "Shadow Trading", status: "blocked", pct: 0,
    desc: "90-day shadow validation with real quote feed. No real money.",
    tasks: [
      { t: "Live quote feed integration", d: false },
      { t: "Shadow order matching", d: false },
      { t: "90-day validation period", d: false },
      { t: "Drift monitoring", d: false },
    ],
    stageNames: [],
  },
  { id: 9, title: "Tiered Live", status: "blocked", pct: 0,
    desc: "Gradual capital deployment after human approval.",
    tasks: [
      { t: "Human approval gate", d: false },
      { t: "Micro lot sizing", d: false },
      { t: "Scale-up ladder", d: false },
      { t: "24/7 monitoring", d: false },
    ],
    stageNames: [],
  },
];

// ── Utility components ───────────────────────────────────────────

const mono = "'SF Mono', 'Cascadia Code', 'JetBrains Mono', 'Fira Code', Consolas, monospace";
const sans = "-apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', system-ui, sans-serif";

const colors = {
  bg: "#08090a", surface: "#111214", surface2: "#1a1b1f", border: "#252830",
  borderLight: "#2d303a", text: "#e8eaed", textMuted: "#8b8f98", textDim: "#5c6070",
  green: "#34d399", greenDim: "#064e3b", greenBg: "#0d2818",
  red: "#f87171", redDim: "#7f1d1d", redBg: "#1c0d0d",
  blue: "#60a5fa", blueDim: "#1e3a5f", blueBg: "#0d1a2e",
  amber: "#fbbf24", amberDim: "#713f12", amberBg: "#1c1508",
  cyan: "#22d3ee",
};

function Pill({ children, color = "green" }) {
  const c = { green: [colors.greenBg, colors.green], red: [colors.redBg, colors.red], blue: [colors.blueBg, colors.blue], amber: [colors.amberBg, colors.amber], dim: [colors.surface2, colors.textDim] };
  const [bg, fg] = c[color] || c.green;
  return <span style={{ display: "inline-block", padding: "2px 9px", borderRadius: "3px", fontSize: "10px", fontWeight: 700, fontFamily: mono, letterSpacing: "0.06em", backgroundColor: bg, color: fg, lineHeight: "18px" }}>{children}</span>;
}

function VerdictPill({ verdict }) {
  const c = { GO: "green", MODIFY: "amber", HOLD: "blue", KILL: "red" };
  return <Pill color={c[verdict] || "dim"}>{verdict}</Pill>;
}

function StatusPill({ status }) {
  const c = { complete: "green", active: "blue", blocked: "dim" };
  const l = { complete: "COMPLETE", active: "IN PROGRESS", blocked: "BLOCKED" };
  return <Pill color={c[status]}>{l[status]}</Pill>;
}

function Bar({ pct, color = colors.blue, h = 4 }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
      <div style={{ flex: 1, height: h, backgroundColor: colors.surface2, borderRadius: h / 2, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", backgroundColor: color, borderRadius: h / 2 }} />
      </div>
      <span style={{ fontSize: 11, color: colors.textDim, fontFamily: mono, minWidth: 32, textAlign: "right" }}>{pct}%</span>
    </div>
  );
}

function KPI({ label, value, sub, color = colors.text }) {
  return (
    <div style={{ backgroundColor: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 6, padding: "14px 16px", minWidth: 0 }}>
      <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600, letterSpacing: "0.06em", marginBottom: 6, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color, fontFamily: mono, lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: colors.textMuted, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function Section({ title, children, pad = true }) {
  return (
    <div style={{ marginBottom: 20 }}>
      {title && <div style={{ fontSize: 10, fontWeight: 700, color: colors.textDim, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 10 }}>{title}</div>}
      <div style={{ backgroundColor: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 6, overflow: "hidden", ...(pad ? { padding: "16px 18px" } : {}) }}>{children}</div>
    </div>
  );
}

function TH({ children, align = "left" }) {
  return <th style={{ padding: "9px 14px", textAlign: align, color: colors.textDim, fontWeight: 600, fontSize: 10, letterSpacing: "0.05em", textTransform: "uppercase", borderBottom: `1px solid ${colors.border}` }}>{children}</th>;
}

function TD({ children, mono: isMono, align = "left", color: c }) {
  return <td style={{ padding: "9px 14px", textAlign: align, fontSize: 12, fontFamily: isMono ? mono : sans, color: c || colors.text, borderBottom: `1px solid ${colors.surface2}` }}>{children}</td>;
}

function MiniBar({ value, max, color = colors.blue }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div style={{ width: 60, height: 4, backgroundColor: colors.surface2, borderRadius: 2, overflow: "hidden", display: "inline-block", verticalAlign: "middle", marginLeft: 6 }}>
      <div style={{ width: `${pct}%`, height: "100%", backgroundColor: color, borderRadius: 2 }} />
    </div>
  );
}

// ── Tab content ──────────────────────────────────────────────────

function CommandTab() {
  const totalTasks = PHASES.reduce((a, p) => a + p.tasks.length, 0);
  const doneTasks = PHASES.reduce((a, p) => a + p.tasks.filter(t => t.d).length, 0);
  const overallPct = Math.round((doneTasks / totalTasks) * 100);
  const okStages = STAGES.filter(s => s.status === "OK").length;
  const jarvisAction = JARVIS.premarket.action;
  const [jarvisPillColor, jarvisHex] = jarvisActionColor(jarvisAction);
  const gradeColor =
    JARVIS.checklist.score >= 0.85 ? colors.green
    : JARVIS.checklist.score >= 0.60 ? colors.amber
    : colors.red;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 10, marginBottom: 20 }}>
        <KPI label="Jarvis" value={jarvisAction} sub={`${(JARVIS.premarket.confidence * 100).toFixed(0)}% conf`} color={jarvisHex} />
        <KPI label="Discipline" value={`${JARVIS.checklist.discipline}/10`} sub={`grade ${JARVIS.checklist.letter}`} color={gradeColor} />
        <KPI label="Roadmap" value={`${overallPct}%`} sub={`${doneTasks}/${totalTasks} tasks`} color={colors.blue} />
        <KPI label="Orchestrator" value={`${okStages}/${STAGES.length}`} sub="stages green" color={colors.green} />
        <KPI label="Sim PnL" value={`$${SIM.netPnl.toFixed(0)}`} sub={`${SIM.roundTrips} trades / ${SIM.days}d`} color={SIM.netPnl >= 0 ? colors.green : colors.red} />
        <KPI label="Win Rate" value={`${SIM.winRate}%`} sub={`expectancy $${SIM.expectancy}`} color={colors.amber} />
        <KPI label="Firm Verdict" value={VERDICT.stages[5].verdict} sub={VERDICT.strategy} color={colors.red} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <Section title="Firm Agent Pipeline">
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {VERDICT.stages.map((s, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ width: 16, fontSize: 10, color: colors.textDim, fontFamily: mono, textAlign: "right" }}>{i + 1}</span>
                  <span style={{ width: 80, fontSize: 12, fontWeight: 600, color: colors.text }}>{s.name}</span>
                  <span style={{ width: 72, fontSize: 10, color: colors.textDim }}>{s.role}</span>
                  <VerdictPill verdict={s.verdict} />
                  <span style={{ fontSize: 11, color: colors.textDim, fontFamily: mono }}>{s.prob.toFixed(2)}</span>
                  <span style={{ flex: 1, fontSize: 11, color: colors.textMuted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.reasoning.split(";")[0]}</span>
                </div>
              ))}
            </div>
          </Section>

          <Section title="System Health">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {[
                { k: "Bridge", v: SYSTEM.firmReady ? "CONNECTED" : "DISCONNECTED", c: SYSTEM.firmReady ? "green" : "red" },
                { k: "Contract", v: `${SYSTEM.firmContract.agents} agents`, c: "green" },
                { k: "Tests", v: `${SYSTEM.testsPass} pass, ${SYSTEM.testsSkip} skip`, c: "green" },
                { k: "Registry", v: `${SYSTEM.registryVariants} variants`, c: "blue" },
                { k: "Reconcile", v: `${SIM.reconcileDiffs} diffs`, c: "green" },
                { k: "Turnover z", v: `${SIM.turnoverZ.toFixed(2)}`, c: Math.abs(SIM.turnoverZ) < 3 ? "green" : "red" },
              ].map((r, i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 11, color: colors.textMuted }}>{r.k}</span>
                  <Pill color={r.c}>{r.v}</Pill>
                </div>
              ))}
            </div>
          </Section>
        </div>

        <div>
          <Section title="Regime Performance (20-day sim)">
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr><TH>Regime</TH><TH align="right">Trades</TH><TH align="right">WR</TH><TH align="right">Net PnL</TH><TH>Edge</TH></tr></thead>
              <tbody>
                {REGIME_PNL.map((r, i) => (
                  <tr key={i}>
                    <TD mono>{r.regime}</TD>
                    <TD align="right" mono>{r.trades}</TD>
                    <TD align="right" mono color={r.wr >= 50 ? colors.green : colors.red}>{r.wr.toFixed(0)}%</TD>
                    <TD align="right" mono color={r.pnl >= 0 ? colors.green : colors.red}>${r.pnl.toFixed(2)}</TD>
                    <TD><MiniBar value={Math.max(0, r.pnl)} max={60} color={r.pnl >= 0 ? colors.green : colors.red} /></TD>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section title="Firm Filter Justification">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600, letterSpacing: "0.05em", marginBottom: 6 }}>FILTERED</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: colors.green, fontFamily: mono }}>${FIRM_FILTER.filteredPnl.toFixed(0)}</div>
                <div style={{ fontSize: 11, color: colors.textMuted }}>{FIRM_FILTER.filteredTrades} trades, {FIRM_FILTER.filteredWR}% WR</div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600, letterSpacing: "0.05em", marginBottom: 6 }}>BASELINE</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: colors.red, fontFamily: mono }}>${FIRM_FILTER.baselinePnl.toFixed(0)}</div>
                <div style={{ fontSize: 11, color: colors.textMuted }}>{FIRM_FILTER.baselineTrades} trades, {FIRM_FILTER.baselineWR}% WR</div>
              </div>
            </div>
            <div style={{ marginTop: 12, padding: "8px 10px", backgroundColor: colors.greenBg, borderRadius: 4 }}>
              <span style={{ fontSize: 11, color: colors.green, fontWeight: 600 }}>LIFT: +${FIRM_FILTER.lift.toFixed(0)}</span>
              <span style={{ fontSize: 11, color: colors.textMuted, marginLeft: 10 }}>95% CI: ${FIRM_FILTER.ciLow} / ${FIRM_FILTER.ciHigh}</span>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

function RoadmapTab() {
  const [expanded, setExpanded] = useState(new Set([1, 3, 5]));
  const toggle = (id) => setExpanded(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const totalTasks = PHASES.reduce((a, p) => a + p.tasks.length, 0);
  const doneTasks = PHASES.reduce((a, p) => a + p.tasks.filter(t => t.d).length, 0);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 20 }}>
        <div style={{ fontSize: 28, fontWeight: 700, fontFamily: mono, color: colors.text }}>{Math.round((doneTasks / totalTasks) * 100)}%</div>
        <div style={{ flex: 1 }}><Bar pct={Math.round((doneTasks / totalTasks) * 100)} color={colors.blue} h={6} /></div>
        <span style={{ fontSize: 11, color: colors.textMuted }}>{doneTasks}/{totalTasks} tasks</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {PHASES.map(p => {
          const isOpen = expanded.has(p.id);
          const bc = p.status === "complete" ? colors.greenDim : p.status === "active" ? colors.blueDim : colors.border;
          const phaseStages = STAGES.filter(s => p.stageNames.includes(s.name));
          return (
            <div key={p.id} onClick={() => toggle(p.id)} style={{ backgroundColor: colors.surface, border: `1px solid ${bc}`, borderRadius: 6, padding: "14px 18px", cursor: "pointer" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ fontSize: 11, fontFamily: mono, color: colors.textDim, fontWeight: 700, width: 18 }}>{p.id}</span>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>{p.title}</span>
                  <span style={{ fontSize: 11, color: colors.textDim }}>{isOpen ? "▾" : "▸"}</span>
                </div>
                <StatusPill status={p.status} />
              </div>
              <div style={{ marginLeft: 28 }}><Bar pct={p.pct} color={p.status === "complete" ? colors.green : colors.blue} /></div>
              {isOpen && (
                <div style={{ marginLeft: 28, marginTop: 12 }}>
                  <div style={{ fontSize: 12, color: colors.textMuted, marginBottom: 10, lineHeight: 1.5 }}>{p.desc}</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    {p.tasks.map((t, i) => (
                      <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ width: 14, fontSize: 12, textAlign: "center", color: t.d ? colors.green : colors.textDim }}>{t.d ? "✓" : "○"}</span>
                        <span style={{ fontSize: 12, color: t.d ? colors.green : colors.textDim }}>{t.t}</span>
                      </div>
                    ))}
                  </div>
                  {phaseStages.length > 0 && (
                    <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 5 }}>
                      {phaseStages.map((s, i) => (
                        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 8px", backgroundColor: s.status === "OK" ? colors.greenBg : colors.redBg, borderRadius: 3, fontSize: 11, fontFamily: mono, color: s.status === "OK" ? colors.green : colors.red }}>
                          {s.status === "OK" ? "●" : "✗"} {s.name} <span style={{ color: colors.textDim }}>({s.dur.toFixed(1)}s)</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function OrchestratorTab() {
  const ok = STAGES.filter(s => s.status === "OK").length;
  const totalDur = STAGES.reduce((a, s) => a + s.dur, 0);
  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
        <div style={{ flex: 1, padding: "12px 16px", backgroundColor: ok === STAGES.length ? colors.greenBg : colors.redBg, border: `1px solid ${ok === STAGES.length ? colors.greenDim : colors.redDim}`, borderRadius: 6, display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 16, color: ok === STAGES.length ? colors.green : colors.red }}>●</span>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: ok === STAGES.length ? colors.green : colors.red }}>{ok}/{STAGES.length} Stages Passed</div>
            <div style={{ fontSize: 11, color: colors.textMuted }}>{SYSTEM.lastRun} — {totalDur.toFixed(1)}s total</div>
          </div>
        </div>
      </div>

      <Section pad={false}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><TH>#</TH><TH>Phase</TH><TH>Stage</TH><TH>Status</TH><TH align="right">Duration</TH><TH>Share</TH></tr></thead>
          <tbody>
            {STAGES.map(s => (
              <tr key={s.n}>
                <TD mono color={colors.textDim}>{s.n}</TD>
                <TD color={colors.textMuted}>{s.phase}</TD>
                <TD mono>{s.name}</TD>
                <TD><Pill color={s.status === "OK" ? "green" : "red"}>{s.status}</Pill></TD>
                <TD align="right" mono color={colors.textMuted}>{s.dur.toFixed(1)}s</TD>
                <TD><MiniBar value={s.dur} max={5} color={colors.blue} /></TD>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}

function VerdictTab() {
  const [selectedStage, setSelectedStage] = useState(null);
  const detail = selectedStage !== null ? VERDICT.stages[selectedStage] : null;

  return (
    <div>
      {/* PM banner */}
      <div style={{ padding: "20px 22px", backgroundColor: colors.redBg, border: `1px solid ${colors.redDim}`, borderRadius: 6, marginBottom: 16 }}>
        <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 700, letterSpacing: "0.06em", marginBottom: 4 }}>PM FINAL VERDICT — {VERDICT.strategy}</div>
        <div style={{ fontSize: 32, fontWeight: 800, color: colors.red, fontFamily: mono, letterSpacing: "-0.02em" }}>KILL</div>
        <div style={{ fontSize: 12, color: colors.textMuted, marginTop: 4, lineHeight: 1.5 }}>Red Team filed mandatory dissent with 4 critical attacks. 1 KILL verdict triggers automatic pipeline rejection.</div>
      </div>

      {/* Spec payload */}
      <Section title="Strategy Spec Fed to Firm">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
          {[
            { k: "Sample Size", v: VERDICT.specPayload.sample_size, c: colors.red },
            { k: "Expectancy", v: `${VERDICT.specPayload.expected_expectancy_r}R`, c: colors.amber },
            { k: "OOS Degradation", v: `${VERDICT.specPayload.oos_degradation_pct.toFixed(1)}%`, c: colors.red },
            { k: "DD Kill Switch", v: `${VERDICT.specPayload.dd_kill_switch_r}R`, c: colors.text },
          ].map((item, i) => (
            <div key={i}>
              <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600, letterSpacing: "0.05em", marginBottom: 4 }}>{item.k}</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: item.c, fontFamily: mono }}>{item.v}</div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 4 }}>
          {[
            { k: "Entry", v: VERDICT.specPayload.entry_logic },
            { k: "Stop", v: VERDICT.specPayload.stop_logic },
            { k: "Target", v: VERDICT.specPayload.target_logic },
          ].map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 8 }}>
              <span style={{ fontSize: 10, color: colors.textDim, fontWeight: 600, minWidth: 44 }}>{r.k}</span>
              <span style={{ fontSize: 11, color: colors.textMuted, fontFamily: mono }}>{r.v}</span>
            </div>
          ))}
        </div>
      </Section>

      {/* Stage pipeline */}
      <Section title="Six-Stage Adversarial Review" pad={false}>
        {VERDICT.stages.map((s, i) => (
          <div key={i} onClick={() => setSelectedStage(selectedStage === i ? null : i)}
            style={{ display: "flex", alignItems: "center", padding: "12px 18px", borderBottom: i < 5 ? `1px solid ${colors.surface2}` : "none", cursor: "pointer", backgroundColor: selectedStage === i ? colors.surface2 : "transparent" }}>
            <div style={{ width: 26, height: 26, borderRadius: "50%", backgroundColor: colors.surface2, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: colors.textDim, fontWeight: 700, marginRight: 14 }}>{i + 1}</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{s.name} <span style={{ fontSize: 10, color: colors.textDim, fontWeight: 400, marginLeft: 6 }}>{s.role}</span></div>
              <div style={{ fontSize: 11, color: colors.textMuted, marginTop: 2, maxWidth: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.reasoning}</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 11, fontFamily: mono, color: colors.textDim }}>{s.prob.toFixed(2)}</span>
              <span style={{ fontSize: 11, fontFamily: mono, color: colors.textDim }}>({s.ci[0].toFixed(2)}/{s.ci[1].toFixed(2)})</span>
              <VerdictPill verdict={s.verdict} />
            </div>
          </div>
        ))}
      </Section>

      {/* Detail panel */}
      {detail && (
        <Section title={`${detail.name} — Detail`}>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div><span style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>REASONING: </span><span style={{ fontSize: 12, color: colors.textMuted }}>{detail.reasoning}</span></div>
            <div><span style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>FALSIFICATION: </span><span style={{ fontSize: 12, color: colors.textMuted }}>{detail.falsification}</span></div>
            {detail.attacks.length > 0 && (
              <div>
                <span style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>ATTACKS / VIOLATIONS:</span>
                <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                  {detail.attacks.map((a, j) => (
                    <div key={j} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", backgroundColor: colors.redBg, borderRadius: 3 }}>
                      <span style={{ fontSize: 11, color: colors.red }}>!</span>
                      <span style={{ fontSize: 11, color: colors.textMuted }}>{a}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Section>
      )}
    </div>
  );
}

function RiskTab() {
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 20 }}>
        <KPI label="Avg Slippage" value={`${SIM.avgSlippage.toFixed(1)}t`} sub={`median ${SIM.medianSlippage}t`} color={colors.amber} />
        <KPI label="p95 Adverse" value={`${SIM.p95Slippage}t`} color={colors.red} />
        <KPI label="Turnover/Day" value={SIM.turnoverRealized.toFixed(2)} sub={`z = ${SIM.turnoverZ.toFixed(2)}`} color={colors.text} />
        <KPI label="Reconcile" value={`${SIM.reconcileDiffs}`} sub="diffs" color={colors.green} />
        <KPI label="Risk Blocks" value={`${SIM.blockedByRisk}`} sub="breaker halts: 0" color={colors.green} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <Section title="Bayesian Posterior (Win Rate by Bucket)" pad={false}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr><TH>Regime</TH><TH>Side</TH><TH align="right">n</TH><TH align="right">WR</TH><TH align="right">Post. Exp</TH><TH>Heat</TH></tr></thead>
              <tbody>
                {BAYESIAN.map((b, i) => (
                  <tr key={i}>
                    <TD mono>{b.regime}</TD>
                    <TD mono color={b.side === "long" ? colors.green : colors.red}>{b.side}</TD>
                    <TD align="right" mono>{b.n}</TD>
                    <TD align="right" mono color={b.wr >= 50 ? colors.green : colors.red}>{b.wr.toFixed(0)}%</TD>
                    <TD align="right" mono color={b.postExp >= 0 ? colors.green : colors.red}>${b.postExp.toFixed(2)}</TD>
                    <TD><Pill color={b.heat > 0 ? "green" : "red"}>{b.heat > 0 ? "OPEN" : "CAPPED"}</Pill></TD>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        </div>

        <div>
          <Section title="Calibration (ML Scorer)" pad={false}>
            <div style={{ padding: "14px 18px" }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
                {[
                  { k: "Brier (IS)", v: CALIBRATION.brierIS.toFixed(4), c: colors.green },
                  { k: "Brier (LOOCV)", v: CALIBRATION.brierLOOCV.toFixed(4), c: CALIBRATION.brierLOOCV < 0.3 ? colors.amber : colors.red },
                  { k: "Log-Loss (IS)", v: CALIBRATION.logLossIS.toFixed(4), c: colors.green },
                  { k: "Log-Loss (LOOCV)", v: CALIBRATION.logLossLOOCV.toFixed(4), c: colors.amber },
                ].map((m, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ fontSize: 11, color: colors.textMuted }}>{m.k}</span>
                    <span style={{ fontSize: 12, fontFamily: mono, fontWeight: 600, color: m.c }}>{m.v}</span>
                  </div>
                ))}
              </div>
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr><TH>Pred Mean</TH><TH align="right">Realized WR</TH><TH align="right">n</TH><TH>Cal</TH></tr></thead>
              <tbody>
                {CALIBRATION.buckets.map((b, i) => {
                  const diff = Math.abs(b.pred - b.realized);
                  return (
                    <tr key={i}>
                      <TD mono>{b.pred.toFixed(3)}</TD>
                      <TD align="right" mono>{(b.realized * 100).toFixed(0)}%</TD>
                      <TD align="right" mono>{b.n}</TD>
                      <TD><Pill color={diff < 0.15 ? "green" : diff < 0.3 ? "amber" : "red"}>{diff < 0.15 ? "GOOD" : diff < 0.3 ? "FAIR" : "POOR"}</Pill></TD>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Section>
        </div>
      </div>
    </div>
  );
}

function WalkForwardTab() {
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 20 }}>
        <KPI label="OOS PnL" value={`$${WALKFORWARD.totalTestPnl}`} sub={`${WALKFORWARD.totalTestTrades} trades`} color={WALKFORWARD.totalTestPnl >= 0 ? colors.green : colors.red} />
        <KPI label="Mean / Fold" value={`$${WALKFORWARD.meanPnlPerFold.toFixed(1)}`} sub={`stdev $${WALKFORWARD.stdevPnlPerFold.toFixed(1)}`} color={colors.amber} />
        <KPI label="Positive Folds" value={`${WALKFORWARD.positiveFolds}/${WALKFORWARD.folds}`} color={colors.blue} />
        <KPI label="Winner Stability" value={WALKFORWARD.winnerStability} sub="r5_real_wide_target" color={colors.green} />
        <KPI label="Config" value={`${WALKFORWARD.trainWindow}/${WALKFORWARD.testWindow}/${WALKFORWARD.stride}`} sub="train/test/stride" color={colors.text} />
      </div>

      <Section title="Fold-by-Fold Results" pad={false}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><TH>Fold</TH><TH align="right">Train PnL</TH><TH align="right">Test PnL</TH><TH align="right">Test n</TH><TH align="right">Test WR</TH><TH>Result</TH></tr></thead>
          <tbody>
            {WALKFORWARD.folds.map(f => (
              <tr key={f.id}>
                <TD mono>{f.id}</TD>
                <TD align="right" mono color={f.trainPnl >= 0 ? colors.green : colors.red}>${f.trainPnl.toFixed(1)}</TD>
                <TD align="right" mono color={f.testPnl >= 0 ? colors.green : colors.red}>${f.testPnl.toFixed(1)}</TD>
                <TD align="right" mono>{f.testN}</TD>
                <TD align="right" mono>{f.testWR}%</TD>
                <TD><Pill color={f.testPnl >= 0 ? "green" : "red"}>{f.testPnl >= 0 ? "PROFIT" : "LOSS"}</Pill></TD>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
        <Section title="Firm Filter Lift (15 days)">
          <div style={{ display: "flex", gap: 20, marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600, letterSpacing: "0.05em" }}>FILTERED</div>
              <div style={{ fontSize: 20, fontWeight: 700, fontFamily: mono, color: colors.green }}>${FIRM_FILTER.filteredPnl}</div>
              <div style={{ fontSize: 11, color: colors.textMuted }}>{FIRM_FILTER.filteredTrades} trades @ ${ FIRM_FILTER.filteredExp}/trade</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600, letterSpacing: "0.05em" }}>BASELINE</div>
              <div style={{ fontSize: 20, fontWeight: 700, fontFamily: mono, color: colors.red }}>${FIRM_FILTER.baselinePnl}</div>
              <div style={{ fontSize: 11, color: colors.textMuted }}>{FIRM_FILTER.baselineTrades} trades @ ${FIRM_FILTER.baselineExp}/trade</div>
            </div>
          </div>
          <div style={{ padding: "8px 10px", backgroundColor: colors.greenBg, borderRadius: 4, display: "flex", gap: 16 }}>
            <span style={{ fontSize: 12, fontWeight: 700, fontFamily: mono, color: colors.green }}>+${FIRM_FILTER.lift} lift</span>
            <span style={{ fontSize: 11, color: colors.textMuted }}>95% CI: ${FIRM_FILTER.ciLow} / ${FIRM_FILTER.ciHigh}</span>
          </div>
        </Section>

        <Section title="Architecture">
          <div style={{ fontFamily: mono, fontSize: 11, color: colors.textMuted, lineHeight: 1.7, whiteSpace: "pre" }}>{
`mnq_bot (execution)
  ├─ specs/        YAML → Pine + Python
  ├─ src/mnq/      core, features, sim, executor
  │   └─ firm_runtime.py  ← auto-generated shim
  ├─ scripts/      21 operational scripts
  └─ reports/      generated analysis
       ↕  bridge (probe → shim → fallback)
the_firm (adversarial agents)
  └─ firm/
      ├─ agents/   Quant→RT→Risk→Macro→Micro→PM
      ├─ regime.py 5-axis classifier → 9 regimes
      ├─ data/     universe (25+ symbols)
      └─ types.py  Verdict, Quadrant, StrategySpec`}</div>
        </Section>
      </div>
    </div>
  );
}

// ── Jarvis (Financial Jarvis — principles loop) ─────────────────

function jarvisActionColor(action) {
  // Returns [pill color, raw hex] for a given action keyword.
  switch (action) {
    case "TRADE":       return ["green", colors.green];
    case "STAND_ASIDE": return ["amber", colors.amber];
    case "REDUCE":      return ["amber", colors.amber];
    case "REVIEW":      return ["blue",  colors.blue];
    case "KILL":        return ["red",   colors.red];
    default:            return ["dim",   colors.textDim];
  }
}

function JarvisActionBanner({ pm }) {
  const [pillColor, hex] = jarvisActionColor(pm.action);
  const bg = pillColor === "green" ? colors.greenBg
           : pillColor === "red"   ? colors.redBg
           : pillColor === "amber" ? colors.amberBg
           : pillColor === "blue"  ? colors.blueBg
           : colors.surface2;
  const border = pillColor === "green" ? colors.greenDim
               : pillColor === "red"   ? colors.redDim
               : pillColor === "amber" ? colors.amberDim
               : pillColor === "blue"  ? colors.blueDim
               : colors.border;
  return (
    <div style={{ padding: "18px 22px", backgroundColor: bg, border: `1px solid ${border}`, borderRadius: 6, marginBottom: 16 }}>
      <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 4 }}>
        JARVIS v2 — {pm.ts.replace("T", " ").replace("Z", " UTC")}{pm.sessionPhase ? ` — ${pm.sessionPhase}` : ""}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
        <div style={{ fontSize: 30, fontWeight: 800, color: hex, fontFamily: mono, letterSpacing: "-0.02em" }}>{pm.action}</div>
        <div style={{ fontSize: 12, color: colors.textMuted }}>{pm.reason}</div>
        <div style={{ fontSize: 11, color: colors.textDim, fontFamily: mono, marginLeft: "auto" }}>confidence {(pm.confidence * 100).toFixed(0)}%</div>
      </div>
      {pm.explanation && (
        <div style={{ marginTop: 10, fontSize: 12, color: colors.textMuted, lineHeight: 1.5 }}>
          {pm.explanation}
        </div>
      )}
      {pm.warnings.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 4 }}>
          {pm.warnings.map((w, i) => (
            <div key={i} style={{ fontSize: 11, color: hex, fontFamily: mono }}>● {w}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function alertLevelColor(level) {
  switch (level) {
    case "CRITICAL": return ["red",   colors.red];
    case "WARN":     return ["amber", colors.amber];
    case "INFO":     return ["blue",  colors.blue];
    default:         return ["dim",   colors.textDim];
  }
}

function StressBar({ component }) {
  const pct = component.value * 100;
  const color = component.value >= 0.8 ? colors.red
              : component.value >= 0.5 ? colors.amber
              : component.value >= 0.2 ? colors.blue
              : colors.green;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "140px 1fr 90px 120px", gap: 8, alignItems: "center", padding: "4px 0" }}>
      <span style={{ fontSize: 11, color: colors.textMuted, fontFamily: mono }}>{component.name}</span>
      <div style={{ height: 6, backgroundColor: colors.surface2, borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", backgroundColor: color }} />
      </div>
      <span style={{ fontSize: 10, color: colors.textDim, fontFamily: mono, textAlign: "right" }}>
        w={component.weight.toFixed(2)} · ctr={component.contribution.toFixed(3)}
      </span>
      <span style={{ fontSize: 10, color: colors.textDim, fontFamily: mono, textAlign: "right" }}>{component.note}</span>
    </div>
  );
}

function StressScorePanel({ stress, binding }) {
  if (!stress) return null;
  const composite = stress.composite * 100;
  const color = stress.composite >= 0.8 ? colors.red
              : stress.composite >= 0.5 ? colors.amber
              : stress.composite >= 0.2 ? colors.blue
              : colors.green;
  return (
    <Section title="Stress Score">
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 10, paddingBottom: 10, borderBottom: `1px solid ${colors.surface2}` }}>
        <div style={{ fontSize: 36, fontWeight: 800, color, fontFamily: mono, letterSpacing: "-0.02em" }}>
          {composite.toFixed(0)}%
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <span style={{ fontSize: 11, color: colors.textDim, letterSpacing: "0.08em", fontWeight: 600 }}>BINDING CONSTRAINT</span>
          <span style={{ fontSize: 13, color: colors.text, fontFamily: mono }}>{stress.bindingConstraint}</span>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {[...stress.components].sort((a, b) => b.contribution - a.contribution).map((c, i) => (
          <StressBar key={i} component={c} />
        ))}
      </div>
    </Section>
  );
}

function SizingHintPanel({ sizing }) {
  if (!sizing) return null;
  const pct = sizing.sizeMult * 100;
  const color = sizing.sizeMult >= 0.9 ? colors.green
              : sizing.sizeMult >= 0.5 ? colors.blue
              : sizing.sizeMult > 0 ? colors.amber
              : colors.red;
  return (
    <Section title="Sizing Hint">
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 8 }}>
        <div style={{ fontSize: 32, fontWeight: 800, color, fontFamily: mono, letterSpacing: "-0.02em" }}>
          {pct.toFixed(0)}%
        </div>
        <span style={{ fontSize: 11, color: colors.textDim, letterSpacing: "0.08em", fontWeight: 600 }}>OF BASELINE SIZE</span>
      </div>
      <div style={{ fontSize: 11, color: colors.textMuted, fontFamily: mono, lineHeight: 1.4 }}>
        {sizing.reason}
      </div>
      {sizing.kellyCap != null && (
        <div style={{ fontSize: 11, color: colors.textDim, fontFamily: mono, marginTop: 6 }}>
          Kelly cap: {(sizing.kellyCap * 100).toFixed(0)}%
        </div>
      )}
    </Section>
  );
}

function AlertsPanel({ alerts }) {
  return (
    <Section title={`Alerts (${alerts.length})`}>
      {alerts.length === 0 ? (
        <div style={{ padding: "10px 12px", backgroundColor: colors.greenBg, borderRadius: 4, fontSize: 11, color: colors.green, fontFamily: mono }}>
          no alerts — all gates clear
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {alerts.map((a, i) => {
            const [pillColor, hex] = alertLevelColor(a.level);
            return (
              <div key={i} style={{ padding: "6px 10px", backgroundColor: colors.surface2, borderLeft: `3px solid ${hex}`, borderRadius: 3 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <Pill color={pillColor}>{a.level}</Pill>
                  <span style={{ fontSize: 11, color: colors.text, fontFamily: mono, fontWeight: 600 }}>{a.code}</span>
                  <span style={{ fontSize: 10, color: colors.textDim, fontFamily: mono, marginLeft: "auto" }}>sev {(a.severity * 100).toFixed(0)}%</span>
                </div>
                <div style={{ fontSize: 11, color: colors.textMuted, marginTop: 2 }}>{a.message}</div>
              </div>
            );
          })}
        </div>
      )}
    </Section>
  );
}

function MarginsPanel({ margins }) {
  if (!margins) return null;
  const row = (label, value, unit, positive_good = true) => {
    const isGood = positive_good ? value > 0 : value < 0;
    const color = value > 0 ? colors.green : value < 0 ? colors.red : colors.amber;
    const sign = value >= 0 ? "+" : "";
    return (
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0" }}>
        <span style={{ fontSize: 11, color: colors.textMuted }}>{label}</span>
        <span style={{ fontSize: 12, color, fontFamily: mono, fontWeight: 700 }}>{sign}{value.toFixed(2)}{unit}</span>
      </div>
    );
  };
  return (
    <Section title="Margins to Next Tier">
      {row("DD → REDUCE", margins.ddToReduce * 100, "%")}
      {row("DD → STAND_ASIDE", margins.ddToStandAside * 100, "%")}
      {row("DD → KILL", margins.ddToKill * 100, "%")}
      {row("Overrides → REVIEW", margins.overridesToReview, "")}
      {row("Open Risk → CAP", margins.openRiskToCapR, "R")}
    </Section>
  );
}

function TrajectoryPanel({ trajectory }) {
  if (!trajectory || trajectory.samples === 0) {
    return (
      <Section title="Trajectory">
        <div style={{ padding: "10px 12px", backgroundColor: colors.surface2, borderRadius: 4, fontSize: 11, color: colors.textDim, fontFamily: mono }}>
          not enough history — need ≥ 3 engine ticks
        </div>
      </Section>
    );
  }
  const stateColor = (s) => s === "WORSENING" ? colors.red
                         : s === "IMPROVING" ? colors.green
                         : s === "FLAT" ? colors.blue
                         : colors.textDim;
  return (
    <Section title="Trajectory">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>DRAWDOWN</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: stateColor(trajectory.dd), fontFamily: mono }}>{trajectory.dd}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>STRESS</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: stateColor(trajectory.stress), fontFamily: mono }}>{trajectory.stress}</div>
        </div>
      </div>
      <div style={{ fontSize: 11, color: colors.textDim, fontFamily: mono }}>
        overrides/24h velocity: {trajectory.overridesVelocity24h.toFixed(2)} ·
        samples {trajectory.samples} · window {(trajectory.windowSeconds / 60).toFixed(1)} min
      </div>
    </Section>
  );
}

function PlaybookPanel({ playbook }) {
  if (!playbook || playbook.length === 0) return null;
  return (
    <Section title="Playbook">
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {playbook.map((step, i) => (
          <div key={i} style={{ display: "flex", gap: 8, padding: "6px 10px", backgroundColor: colors.surface2, borderRadius: 3 }}>
            <span style={{ fontSize: 11, color: colors.textDim, fontFamily: mono }}>{i + 1}.</span>
            <span style={{ fontSize: 11, color: colors.text, lineHeight: 1.4 }}>{step}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Chain of Command -- Jarvis as admin of the fleet
// ---------------------------------------------------------------------------

function tierColor(tier) {
  switch (tier) {
    case "KILL":        return colors.red;
    case "STAND_ASIDE": return colors.red;
    case "REDUCE":      return colors.amber;
    case "REVIEW":      return colors.amber;
    case "TRADE":       return colors.green;
    default:            return colors.textDim;
  }
}

function verdictColor(verdict) {
  switch (verdict) {
    case "APPROVED":    return colors.green;
    case "CONDITIONAL": return colors.amber;
    case "DEFERRED":    return colors.amber;
    case "DENIED":      return colors.red;
    default:            return colors.textDim;
  }
}

function ChainOfCommandPanel({ admin }) {
  if (!admin) return null;
  return (
    <Section title={`Chain of Command — ${admin.version}`}>
      {/* Authority banner */}
      <div style={{
        padding: "10px 12px",
        marginBottom: 14,
        background: `linear-gradient(90deg, ${colors.surface2} 0%, rgba(0,0,0,0) 100%)`,
        borderLeft: `3px solid ${colors.blue}`,
        borderRadius: 3,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 28, height: 28, borderRadius: "50%",
            backgroundColor: colors.blueBg,
            display: "flex", alignItems: "center", justifyContent: "center",
            border: `1px solid ${colors.blue}`,
          }}>
            <span style={{ fontSize: 14, fontWeight: 800, color: colors.blue, fontFamily: mono }}>J</span>
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: colors.text }}>JARVIS — sole authority</div>
            <div style={{ fontSize: 10, color: colors.textMuted, fontFamily: mono }}>
              every autonomous subsystem requests approval before acting
            </div>
          </div>
        </div>
      </div>

      {/* Command tree */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {admin.commandTree.map((grp, gi) => (
          <div key={gi}>
            <div style={{
              fontSize: 10, color: colors.textDim, fontWeight: 600,
              textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4,
            }}>{grp.group} · {grp.items.length}</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 4 }}>
              {grp.items.map((it, ii) => (
                <div key={ii} style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "4px 8px", backgroundColor: colors.surface2, borderRadius: 3,
                }}>
                  <span style={{ fontSize: 11, color: colors.text, fontFamily: mono }}>{it.label}</span>
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: colors.textDim, fontFamily: mono }}>{it.mode}</span>
                    <Pill color={
                      it.tier === "TRADE" ? "green"
                      : it.tier === "REDUCE" ? "amber"
                      : it.tier === "REVIEW" ? "amber"
                      : "red"
                    }>{it.tier}</Pill>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function AdminAuditTailPanel({ audit }) {
  if (!audit || audit.length === 0) {
    return (
      <Section title="Admin Audit Tail">
        <div style={{ fontSize: 11, color: colors.textDim, fontFamily: mono, padding: "6px 0" }}>
          no approvals yet -- subsystems have not called JarvisAdmin.request_approval
        </div>
      </Section>
    );
  }
  return (
    <Section title={`Admin Audit Tail — ${audit.length} recent`}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {audit.map((rec, i) => (
          <div key={i} style={{
            padding: "8px 10px", backgroundColor: colors.surface2, borderRadius: 3,
            display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8, alignItems: "center",
          }}>
            <Pill color={
              rec.verdict === "APPROVED" ? "green"
              : rec.verdict === "DENIED" ? "red"
              : "amber"
            }>{rec.verdict}</Pill>
            <div>
              <div style={{ fontSize: 11, color: colors.text, fontFamily: mono }}>
                {rec.subsystem} → {rec.action}
              </div>
              <div style={{ fontSize: 10, color: colors.textMuted, marginTop: 2 }}>
                {rec.reason_code}: {rec.reason}
              </div>
            </div>
            <div style={{ fontSize: 10, color: colors.textDim, fontFamily: mono, textAlign: "right" }}>
              {rec.size_cap_mult != null ? `cap ${(rec.size_cap_mult * 100).toFixed(0)}%` : ""}
              <div>{rec.ts.slice(11, 19)}</div>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function JarvisTab() {
  const pm = JARVIS.premarket;
  const cl = JARVIS.checklist;
  const mo = JARVIS.monthly;

  const ddPct = (pm.equity.ddToday * 100).toFixed(2);
  const overrideRate = pm.journal.executed24h > 0
    ? (pm.journal.overrides24h / (pm.journal.executed24h + pm.journal.blocked24h + pm.journal.overrides24h) * 100)
    : 0;

  const stressPct = pm.stressScore ? (pm.stressScore.composite * 100) : null;
  const sizePct = pm.sizingHint ? (pm.sizingHint.sizeMult * 100) : null;

  return (
    <div>
      <JarvisActionBanner pm={pm} />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(8, 1fr)", gap: 10, marginBottom: 20 }}>
        <KPI label="Discipline" value={`${cl.discipline}/10`} sub={`grade ${cl.letter}`}
             color={cl.score >= 0.85 ? colors.green : cl.score >= 0.60 ? colors.amber : colors.red} />
        <KPI label="Stress" value={stressPct != null ? `${stressPct.toFixed(0)}%` : "—"}
             sub={pm.stressScore ? pm.stressScore.bindingConstraint : "n/a"}
             color={stressPct == null ? colors.textDim : stressPct >= 60 ? colors.red : stressPct >= 30 ? colors.amber : colors.green} />
        <KPI label="Size Hint" value={sizePct != null ? `${sizePct.toFixed(0)}%` : "—"}
             sub={pm.sessionPhase ?? "—"}
             color={sizePct == null ? colors.textDim : sizePct >= 90 ? colors.green : sizePct >= 50 ? colors.blue : sizePct > 0 ? colors.amber : colors.red} />
        <KPI label="Equity" value={`$${pm.equity.equity.toLocaleString()}`} sub={`pnl ${pm.equity.pnlToday >= 0 ? "+" : ""}$${pm.equity.pnlToday.toLocaleString()}`}
             color={pm.equity.pnlToday >= 0 ? colors.green : colors.red} />
        <KPI label="DD Today" value={`${ddPct}%`} sub="cap 5%"
             color={pm.equity.ddToday >= 0.03 ? colors.red : pm.equity.ddToday >= 0.02 ? colors.amber : colors.green} />
        <KPI label="Open Risk" value={`${pm.equity.openRiskR.toFixed(2)}R`} sub={`${pm.equity.positions} pos`}
             color={pm.equity.openRiskR >= 3.0 ? colors.red : colors.text} />
        <KPI label="Overrides" value={`${pm.journal.overrides24h}`} sub={`${overrideRate.toFixed(0)}%`}
             color={pm.journal.overrides24h >= 3 ? colors.red : colors.text} />
        <KPI label="Autopilot" value={pm.journal.mode} sub={pm.journal.killSwitch ? "KILL" : "live"}
             color={pm.journal.killSwitch ? colors.red : pm.journal.mode === "FROZEN" ? colors.red : pm.journal.mode === "REQUIRE_ACK" ? colors.amber : colors.green} />
      </div>

      {/* v2 top row: Stress / Sizing / Alerts */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 16, marginBottom: 16 }}>
        <StressScorePanel stress={pm.stressScore} />
        <SizingHintPanel sizing={pm.sizingHint} />
        <MarginsPanel margins={pm.margins} />
      </div>

      {/* v2 second row: Alerts / Trajectory / Playbook */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginBottom: 16 }}>
        <AlertsPanel alerts={pm.alerts ?? []} />
        <TrajectoryPanel trajectory={pm.trajectory} />
        <PlaybookPanel playbook={pm.playbook} />
      </div>

      {/* v0.1.27 admin row: Chain of Command / Admin Audit Tail */}
      <div style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: 16, marginBottom: 16 }}>
        <ChainOfCommandPanel admin={JARVIS_ADMIN} />
        <AdminAuditTailPanel audit={JARVIS_ADMIN.recentAudit} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <Section title="10 Principles — Weekly Report Card" pad={false}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr><TH>#</TH><TH>Principle</TH><TH>Question</TH><TH align="center">State</TH></tr></thead>
              <tbody>
                {JARVIS_PRINCIPLES.map(p => {
                  const yes = cl.answers[p.idx];
                  const critical = cl.criticalGaps.includes(p.slug);
                  return (
                    <tr key={p.idx}>
                      <TD mono color={colors.textDim}>{p.idx}</TD>
                      <TD mono>{p.slug}</TD>
                      <TD color={colors.textMuted}>{p.q}</TD>
                      <TD align="center">
                        <Pill color={yes ? "green" : critical ? "red" : "amber"}>{yes ? "YES" : "NO"}</Pill>
                      </TD>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div style={{ padding: "10px 18px", borderTop: `1px solid ${colors.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 11, color: colors.textDim }}>period {cl.period}</span>
              <span style={{ fontSize: 14, fontWeight: 700, fontFamily: mono, color: cl.score >= 0.85 ? colors.green : cl.score >= 0.60 ? colors.amber : colors.red }}>
                {(cl.score * 100).toFixed(0)}% · {cl.letter}
              </span>
            </div>
          </Section>

          <Section title="Context Snapshot">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {[
                { k: "Regime", v: `${pm.regime.current} (${(pm.regime.confidence * 100).toFixed(0)}%)`,
                  c: pm.regime.flipped ? "amber" : "green" },
                { k: "Regime flip", v: pm.regime.flipped ? "YES" : "no",
                  c: pm.regime.flipped ? "amber" : "dim" },
                { k: "Macro bias", v: pm.macro.bias,
                  c: pm.macro.bias === "risk_off" ? "red" : pm.macro.bias === "risk_on" ? "green" : "dim" },
                { k: "VIX", v: pm.macro.vix != null ? pm.macro.vix.toFixed(1) : "n/a", c: "dim" },
                { k: "Next event", v: pm.macro.nextEvent ?? "—",
                  c: (pm.macro.hoursToEvent != null && pm.macro.hoursToEvent < 1) ? "red" : "dim" },
                { k: "Corr alert", v: pm.journal.corrAlert ? "YES" : "no",
                  c: pm.journal.corrAlert ? "red" : "dim" },
              ].map((r, i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 11, color: colors.textMuted }}>{r.k}</span>
                  <Pill color={r.c}>{r.v}</Pill>
                </div>
              ))}
            </div>
            {pm.notes.length > 0 && (
              <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px solid ${colors.surface2}`, display: "flex", flexDirection: "column", gap: 4 }}>
                {pm.notes.map((n, i) => (
                  <div key={i} style={{ fontSize: 11, color: colors.textDim, fontFamily: mono }}>· {n}</div>
                ))}
              </div>
            )}
          </Section>
        </div>

        <div>
          <Section title="Bundle Status — v0.1.29 Jarvis Admin CC">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
              <KPI label="Modules" value={`${JARVIS.bundle.moduleCount}/11`} sub="shipped" color={colors.green} />
              <KPI label="Tests" value={`${JARVIS.bundle.testCount}`} sub={`green · ${JARVIS.bundle.shippedVersion}`} color={colors.green} />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {JARVIS.bundle.moduleNames.map((m, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 14, fontSize: 12, textAlign: "center", color: colors.green }}>✓</span>
                  <span style={{ fontSize: 11, color: colors.text, fontFamily: mono }}>{m}</span>
                </div>
              ))}
            </div>
          </Section>

          <Section title={`Monthly Deep Review — ${mo.period}`}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginBottom: 12 }}>
              <div>
                <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>GRADED TRADES</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: colors.text, fontFamily: mono }}>{mo.gradingN}</div>
                {mo.meanTotal != null && <div style={{ fontSize: 11, color: colors.textMuted }}>mean total {mo.meanTotal.toFixed(2)}</div>}
              </div>
              <div>
                <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>EXIT QUALITY</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: colors.text, fontFamily: mono }}>{mo.exitQualityN}</div>
                <div style={{ fontSize: 11, color: colors.textMuted }}>MAE/MFE rows</div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: colors.textDim, fontWeight: 600 }}>RATIONALES</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: colors.text, fontFamily: mono }}>{mo.rationalesN}</div>
                <div style={{ fontSize: 11, color: colors.textMuted }}>mined</div>
              </div>
            </div>
            {mo.proposedTweaks.length === 0
              ? <div style={{ padding: "10px 12px", backgroundColor: colors.surface2, borderRadius: 4, fontSize: 11, color: colors.textDim, fontFamily: mono }}>no tweaks proposed this cycle</div>
              : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {mo.proposedTweaks.map((t, i) => (
                    <div key={i} style={{ padding: "8px 10px", backgroundColor: colors.amberBg, borderRadius: 4 }}>
                      <div style={{ fontSize: 11, color: colors.amber, fontWeight: 600, fontFamily: mono }}>[{t.category}] {t.metric}</div>
                      <div style={{ fontSize: 11, color: colors.textMuted, marginTop: 2 }}>{t.rationale}</div>
                    </div>
                  ))}
                </div>
              )}
          </Section>
        </div>
      </div>
    </div>
  );
}

// ── Integrations tab ─────────────────────────────────────────────
// Surfaces the canonical funnel topology: venues -> bots -> layers ->
// onramps -> staking -> observability. Data comes from
// eta_engine/docs/integrations_latest.json (built by
// `python -m eta_engine.scripts.build_integrations_report`).

function integrationStatusColor(status) {
  if (status === "READY" || status === "ACTIVE" || status === "LIVE") return "green";
  if (status === "PAPER" || status === "DRY_RUN") return "blue";
  if (status === "NEEDS_FUNDING" || status === "PENDING" || status === "DEGRADED") return "amber";
  if (status === "BLOCKED" || status === "KILLED" || status === "OFFLINE") return "red";
  return "dim";
}

function IntegrationsTab() {
  const readyVenues  = INTEGRATIONS.venues.filter(v => v.status === "READY").length;
  const paperBots    = INTEGRATIONS.bots.filter(b => b.status === "PAPER").length;
  const liveBots     = INTEGRATIONS.bots.filter(b => b.status === "LIVE").length;
  const activeObs    = INTEGRATIONS.observability.filter(o => o.status === "ACTIVE").length;
  const dryRunObs    = INTEGRATIONS.observability.filter(o => o.status === "DRY_RUN").length;
  const totalApy     = INTEGRATIONS.staking.reduce((a, s) => a + s.apy, 0);
  const avgApy       = (totalApy / INTEGRATIONS.staking.length).toFixed(1);
  const monthlyUsd   = INTEGRATIONS.onrampRoutes.reduce((a, r) => a + r.monthly, 0);

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 10, marginBottom: 20 }}>
        <KPI label="Venues" value={`${readyVenues}/${INTEGRATIONS.venues.length}`} sub="ready / total" color={colors.green} />
        <KPI label="Bots" value={`${paperBots}P ${liveBots}L`} sub={`${INTEGRATIONS.bots.length} total`} color={colors.blue} />
        <KPI label="Layers" value={INTEGRATIONS.funnelLayers.length} sub="waterfall tiers" color={colors.cyan} />
        <KPI label="Onramps" value={INTEGRATIONS.onrampRoutes.length} sub={`$${(monthlyUsd / 1000).toFixed(0)}k/mo cap`} color={colors.amber} />
        <KPI label="Staking" value={`${avgApy}%`} sub={`${INTEGRATIONS.staking.length} protocols avg APY`} color={colors.green} />
        <KPI label="Observability" value={`${activeObs}/${INTEGRATIONS.observability.length}`} sub={`${dryRunObs} dry-run`} color={activeObs === INTEGRATIONS.observability.length ? colors.green : colors.amber} />
      </div>

      <Section title="Profit Waterfall — Funnel Layers" pad={false}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><TH>Layer</TH><TH>Label</TH><TH>Tier</TH><TH align="right">Sweep</TH><TH align="right">Kill DD</TH><TH align="right">Leverage</TH><TH>Notes</TH></tr></thead>
          <tbody>
            {INTEGRATIONS.funnelLayers.map(l => (
              <tr key={l.id}>
                <TD mono color={colors.cyan}>{l.id}</TD>
                <TD>{l.label}</TD>
                <TD><Pill color={l.tier === "A" ? "green" : l.tier === "B" ? "blue" : l.tier === "CASINO" ? "amber" : "dim"}>{l.tier}</Pill></TD>
                <TD align="right" mono color={colors.textMuted}>{(l.sweep * 100).toFixed(0)}%</TD>
                <TD align="right" mono color={colors.red}>{(l.killDD * 100).toFixed(0)}%</TD>
                <TD align="right" mono color={colors.amber}>{l.lev.toFixed(1)}x</TD>
                <TD color={colors.textMuted}>{l.notes}</TD>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Section title="Bots" pad={false}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><TH>Bot</TH><TH>Venue</TH><TH>Layer</TH><TH>Tier</TH><TH>Status</TH></tr></thead>
            <tbody>
              {INTEGRATIONS.bots.map(b => (
                <tr key={b.name}>
                  <TD mono>{b.name}</TD>
                  <TD color={colors.textMuted}>{b.venue}</TD>
                  <TD mono color={colors.cyan}>{b.layer.replace("LAYER_", "L")}</TD>
                  <TD><Pill color={b.tier === "A" ? "green" : b.tier === "SEED" ? "blue" : b.tier === "CASINO" ? "amber" : "dim"}>{b.tier}</Pill></TD>
                  <TD><Pill color={integrationStatusColor(b.status)}>{b.status}</Pill></TD>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        <Section title="Venues" pad={false}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><TH>Venue</TH><TH>Kind</TH><TH>Assets</TH><TH>Status</TH></tr></thead>
            <tbody>
              {INTEGRATIONS.venues.map(v => (
                <tr key={v.name}>
                  <TD mono>{v.name}</TD>
                  <TD color={colors.textMuted}>{v.kind}</TD>
                  <TD color={colors.textMuted} mono>{v.assets}</TD>
                  <TD><Pill color={integrationStatusColor(v.status)}>{v.status}</Pill></TD>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Section title="Onramp Routes (Fiat → Crypto)" pad={false}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><TH>Fiat</TH><TH>Provider</TH><TH>Crypto</TH><TH align="right">Per-Txn</TH><TH align="right">Monthly</TH></tr></thead>
            <tbody>
              {INTEGRATIONS.onrampRoutes.map((r, i) => (
                <tr key={i}>
                  <TD mono color={colors.amber}>{r.fiat}</TD>
                  <TD mono color={colors.textMuted}>{r.provider}</TD>
                  <TD mono>{r.crypto}</TD>
                  <TD align="right" mono color={colors.textMuted}>${(r.perTxn / 1000).toFixed(0)}k</TD>
                  <TD align="right" mono color={colors.textMuted}>${(r.monthly / 1000).toFixed(0)}k</TD>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        <Section title="Staking (Terminal Sink)" pad={false}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><TH>Protocol</TH><TH>Chain</TH><TH>In → Out</TH><TH align="right">APY</TH></tr></thead>
            <tbody>
              {INTEGRATIONS.staking.map(s => (
                <tr key={s.protocol}>
                  <TD mono>{s.protocol}</TD>
                  <TD color={colors.textMuted}>{s.chain}</TD>
                  <TD mono color={colors.cyan}>{s.assetIn} → {s.assetOut}</TD>
                  <TD align="right" mono color={colors.green}>{s.apy.toFixed(1)}%</TD>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      </div>

      <Section title="Observability Surfaces" pad={false}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><TH>Name</TH><TH>Kind</TH><TH>Status</TH><TH>Notes</TH></tr></thead>
          <tbody>
            {INTEGRATIONS.observability.map(o => (
              <tr key={o.name}>
                <TD mono>{o.name}</TD>
                <TD color={colors.textMuted}>{o.kind}</TD>
                <TD><Pill color={integrationStatusColor(o.status)}>{o.status}</Pill></TD>
                <TD color={colors.textMuted}>{o.notes}</TD>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}

// ── Main layout ──────────────────────────────────────────────────

const TABS = [
  { key: "command", label: "Command Center" },
  { key: "jarvis", label: "Jarvis" },
  { key: "roadmap", label: "Roadmap" },
  { key: "orchestrator", label: "Orchestrator" },
  { key: "verdict", label: "Firm Verdict" },
  { key: "risk", label: "Risk & Calibration" },
  { key: "walkforward", label: "Walk-Forward" },
  { key: "integrations", label: "Integrations" },
];

export default function FirmCommandCenter() {
  const [tab, setTab] = useState("command");

  const content = {
    command: <CommandTab />,
    jarvis: <JarvisTab />,
    roadmap: <RoadmapTab />,
    orchestrator: <OrchestratorTab />,
    verdict: <VerdictTab />,
    risk: <RiskTab />,
    walkforward: <WalkForwardTab />,
    integrations: <IntegrationsTab />,
  };

  const [jarvisPillColor] = jarvisActionColor(JARVIS.premarket.action);

  return (
    <div style={{ backgroundColor: colors.bg, color: colors.text, minHeight: "100vh", fontFamily: sans }}>
      {/* Top bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 28px", borderBottom: `1px solid ${colors.border}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: colors.green }} />
          <span style={{ fontSize: 15, fontWeight: 700, letterSpacing: "0.1em", fontFamily: mono }}>THE FIRM</span>
          <span style={{ fontSize: 11, color: colors.textDim, fontFamily: mono }}>MNQ COMMAND CENTER</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <Pill color={jarvisPillColor}>JARVIS {JARVIS.premarket.action}</Pill>
          <Pill color="green">BRIDGE ACTIVE</Pill>
          <Pill color="green">{SYSTEM.firmContract.agents} AGENTS</Pill>
          <Pill color="green">{SYSTEM.testsPass} TESTS</Pill>
          <span style={{ fontSize: 11, color: colors.textDim, fontFamily: mono }}>{SYSTEM.lastRun}</span>
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", borderBottom: `1px solid ${colors.border}`, padding: "0 28px" }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} style={{
            padding: "11px 18px", fontSize: 12, fontWeight: 600, fontFamily: sans,
            color: tab === t.key ? colors.text : colors.textDim,
            backgroundColor: "transparent", border: "none",
            borderBottom: tab === t.key ? `2px solid ${colors.cyan}` : "2px solid transparent",
            cursor: "pointer",
          }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ padding: "24px 28px" }}>
        {content[tab]}
      </div>
    </div>
  );
}
