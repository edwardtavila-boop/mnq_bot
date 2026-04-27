# QUICKSTART — Apex V3 Framework

5-minute guide to validate the system on your own data.

## Prerequisites

- Python 3.9+
- Databento account with $125 free credits (free signup at databento.com)
- ~50MB free disk for data
- 5 minutes

## Step 1: Install

```bash
cd eta_v3_framework
pip install -r requirements.txt
```

## Step 2: Set your Databento API key

Sign up at https://databento.com (no credit card needed for free tier).
Then in the portal: API Keys → Create new key.

```bash
export DATABENTO_API_KEY="db-your-key-here"
```

## Step 3: Verify with sample data

A small NQ data sample is included to verify the pipeline works without spending credits:

```bash
cd python/
python edge_discovery.py ../data_samples/nq_5m_sample.csv --pm 25
```

This should produce trade log and decomposition CSVs. If this works, the pipeline is functional.

## Step 4: Pull real historical data

```bash
# Estimate cost first (no charge)
python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 --estimate

# Should show: ~1.15M records, ~$4.20 cost
# You have $125 free credits

# Pull 3 years of NQ 5m bars
mkdir -p ../data
python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 \
    --out ../data/nq_5m.csv --resample 5m
```

## Step 5: Generate trade log

```bash
python edge_discovery.py ../data/nq_5m.csv --pm 25 --out-dir ../results/
```

This produces:
- `../results/trades_full.csv` — Master trade log (193 trades expected)
- `../results/edge_findings.md` — Human-readable decomposition
- `../results/by_*.csv` — Cross-tab analyses

## Step 6: Run V3 Final

```bash
python v3_final.py ../results/trades_full.csv
```

Expected output: ~58 trades over 3 years, +13R total, PF 4-5, MDD <1R.

## Step 7: Validate with optimization

```bash
python optimize_v3.py ../results/trades_full.csv
```

Will run walk-forward search across 972 parameter combinations and confirm:
1. Optimal parameters match the documented defaults
2. OOS test outperforms (or matches) train
3. Monte Carlo passes all 3 criteria

## Step 8: (Optional) Pull other instruments

```bash
# Add MNQ for direct trading instrument data
python databento_fetcher.py --symbol MNQ --start 2023-01-01 --end 2026-04-14 \
    --out ../data/mnq_5m.csv --resample 5m

# Add ES for V9 intermarket correlation
python databento_fetcher.py --symbol ES --start 2023-01-01 --end 2026-04-14 \
    --out ../data/es_5m.csv --resample 5m

# Or pull all in one shot
python bulk_fetch.py --start 2023-01-01 --end 2026-04-14 \
    --out-dir ../data --symbols NQ MNQ ES MES
```

Total cost: ~$8 of free credits for 3 years of all 4 symbols.

## Going live (next 60 days)

1. Load `pine/MNQ_ETA_v2_Firm.pine` into TradingView
2. Configure alerts on signal events
3. Run `live_deployment/webhook.py` to receive alerts
4. **Paper trade only.** Track every signal's score and outcome
5. Compare paper P&L to backtest projections weekly
6. Only consider real capital after 30+ paper-traded winners

## Troubleshooting

**"DATABENTO_API_KEY not set"** — `export DATABENTO_API_KEY="db-xxx"` before running

**"License not activated"** — Go to portal → Datasets → GLBX.MDP3 → fill questionnaire (free for non-pro)

**"No trades produced"** — Check date range covers regular trading hours. Your data must include U.S. RTH (9:30-16:00 ET) on weekdays.

**Slow Monte Carlo** — Reduce `--mc-sims` or `--sims` parameter to 1000 for quick checks.

**Weird CSV format errors** — All CSVs must have header `time,open,high,low,close,volume` with `time` as Unix epoch seconds. The Databento fetcher handles this automatically.

## Where to go next

- Read `docs/V3_FINAL_DASHBOARD.md` for detailed performance breakdown
- Read `docs/EDGE_SPEC_V2.md` to understand the data-derived rules
- Read `docs/EDGE_FINDINGS_RAW.md` for the raw analytical findings
- Modify `confluence_scorer.py` weights only after consulting decomposition data
