# Databento Setup Guide — Apex v2

## Step 1: Sign up

1. Go to **https://databento.com**
2. Click "Sign up" (takes under 2 minutes, no sales call)
3. Confirm email, log in to the portal
4. You'll automatically get **$125 in free credits**

## Step 2: Activate CME license

1. In the portal, go to **Datasets → GLBX.MDP3** (CME Globex)
2. Complete the short licensing questionnaire (non-professional trader = free)
3. For non-pro traders, CME data is typically **$0/month in license fees**
4. License activates within minutes

## Step 3: Get your API key

1. In the portal, go to **API Keys** (or https://databento.com/portal/keys)
2. Click "Create new API key"
3. Copy the key (starts with `db-...`)
4. Save it to your environment:

```bash
# Mac/Linux - add to ~/.zshrc or ~/.bashrc
export DATABENTO_API_KEY="db-your-key-here"

# Or set for current session
export DATABENTO_API_KEY="db-your-key-here"

# Windows PowerShell
$env:DATABENTO_API_KEY="db-your-key-here"
```

## Step 4: Install the Python SDK

```bash
pip install databento pandas
```

## Step 5: Test with a cost estimate (no charge)

Before pulling any data, always estimate cost first:

```bash
cd python/
python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 --estimate
```

Expected output:
```
Symbol:   NQ (NQ.c.0)
Dataset:  GLBX.MDP3
Schema:   ohlcv-1m
Range:    2023-01-01 → 2026-04-14

Estimating cost...
  Records:   ~1,200,000
  Est. cost: ~$1.50
```

## Step 6: Pull a test year

Start small — 1 year of NQ to validate the pipeline works:

```bash
python databento_fetcher.py --symbol NQ \
    --start 2025-01-01 --end 2026-04-14 \
    --out nq_5m_1yr.csv --resample 5m
```

This pulls ~450k 1-minute bars and resamples to ~90k 5-minute bars. Cost ~$0.30-0.50.

## Step 7: Backtest on the new dataset

```bash
python backtest.py nq_5m_1yr.csv --pm 25
python monte_carlo.py nq_5m_1yr.csv --pm 25 --sims 2000
python walkforward.py nq_5m_1yr.csv --windows 12 --sweep
```

You should see:
- ~60-100 trades (vs 14 on 73 days)
- Tight Monte Carlo confidence intervals
- 10+ walk-forward windows with meaningful trade counts each

## Step 8: Full bulk pull (when validated)

Once the test pull works, pull everything in one command:

```bash
python bulk_fetch.py --start 2023-01-01 --end 2026-04-14 \
    --out-dir ./historical \
    --symbols NQ MNQ ES MES
```

This pulls 3 years of NQ, MNQ, ES, MES — ~$5-10 total, covered by free credits.

Output files in `./historical/`:
- `nq_5m.csv` — your primary backtest data
- `mnq_5m.csv` — if you want to validate MNQ specifically
- `es_5m.csv` — for V9 ES/NQ correlation voice
- `mes_5m.csv` — supplementary

## Step 9: Run the master validation

With 3 years of aligned NQ+MNQ+ES data:

```bash
# Full Monte Carlo validation
python monte_carlo.py historical/nq_5m.csv --pm 25 --sims 2000 \
    --es historical/es_5m.csv

# Auto-calibrate on real sample size
python autocalibrator.py historical/nq_5m.csv --windows 12

# A/B test all feature combos
python master_test.py historical/nq_5m.csv --pm 25 \
    --es historical/es_5m.csv
```

## Expected cost breakdown

| Pull | Est. records | Est. cost |
|------|-------------|-----------|
| 1 year NQ 1m | ~400k | $0.50 |
| 3 years NQ 1m | ~1.2M | $1.50 |
| 3 years MNQ 1m | ~900k | $1.20 |
| 3 years ES 1m | ~1.2M | $1.50 |
| 3 years MES 1m | ~800k | $1.00 |
| **Total 3-yr bulk** | **~4.1M** | **~$5.20** |

You have $125 in free credits — this leaves $119 for additional pulls, re-pulls, or live data experiments.

## Note on intermarket feeds

For V8 (VIX) and V10 (DXY) / V11 (TICK):

**VIX**: CBOE index, different dataset (`XCBOE.BASIC` on Databento). Pull separately:
```bash
python databento_fetcher.py --symbol "VIX" --start 2023-01-01 --end 2026-04-14 \
    --out vix_5m.csv --resample 5m
```
May require CBOE license — check portal.

**DXY**: ICE dataset (`IFUS.IMPACT`). May have different licensing. Most users skip DXY and rely on 6E (Euro FX) from GLBX instead as a dollar proxy.

**TICK (NYSE TICK)**: NYSE dataset. Less critical voice — you can skip it and use the breadth signal from V9 ES correlation instead.

For your system, NQ + MNQ + ES from GLBX.MDP3 is 90% of the value. VIX adds the final 10% if you want the full V8 voice.

## What if costs exceed free credits?

If you max out the $125 free credits (unlikely for your needs), options are:

- **Pay-as-you-go**: just buy more credits, same prices
- **Standard subscription** ($199/mo): unlimited 7-year OHLCV history for all CME futures, tick data for last 12 months. Good if you're running lots of backtests.
- Most individual traders stay on pay-as-you-go indefinitely because OHLCV data is dirt cheap.

## Troubleshooting

**"License not activated"**: Go to portal, Datasets → GLBX.MDP3, complete the form. Takes ~5 minutes.

**"Symbol not found"**: The `.c.0` suffix means continuous front-month. If you want a specific contract (e.g. March 2025), use raw symbol like `NQH5`.

**Timezone confusion**: Databento returns UTC timestamps as nanoseconds. The fetcher converts to epoch seconds for our CSV format, still UTC. Your backtest's `zoneinfo` conversion to ET handles the rest.

**Out of memory on big pulls**: The fetcher chunks by default at 90 days. Reduce with `--chunk-days 30` if needed.

**Rate limits**: Databento's rate limits are generous for historical fetches. You'd only hit them on rapid repeated pulls of the same data. Our fetcher handles this naturally with chunking.
