#!/bin/bash
# Full Apex V3 pipeline: pull data → analyze → optimize → validate
set -e
cd "$(dirname "$0")/../python"

if [ -z "$DATABENTO_API_KEY" ]; then
    echo "ERROR: DATABENTO_API_KEY not set"
    exit 1
fi

echo "═══ STEP 1: Cost estimate ═══"
python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 --estimate

echo ""
read -p "Proceed with data pull? [y/N]: " confirm
if [ "$confirm" != "y" ]; then exit 0; fi

echo "═══ STEP 2: Pull NQ 3yr data ═══"
python databento_fetcher.py --symbol NQ --start 2023-01-01 --end 2026-04-14 \
    --out ../data/nq_5m.csv --resample 5m --yes

echo ""
echo "═══ STEP 3: Generate trade log + edge analysis ═══"
python edge_discovery.py ../data/nq_5m.csv --pm 25 --out-dir ../results/

echo ""
echo "═══ STEP 4: Run V3 Final ═══"
python v3_final.py ../results/trades_full.csv

echo ""
echo "═══ STEP 5: Walk-forward optimization ═══"
python optimize_v3.py ../results/trades_full.csv

echo ""
echo "════════════════════════════════════════"
echo "PIPELINE COMPLETE"
echo "Results in: ../results/"
echo "Data in: ../data/"
echo "════════════════════════════════════════"
