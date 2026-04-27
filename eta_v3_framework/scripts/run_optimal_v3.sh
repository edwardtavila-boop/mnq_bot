#!/bin/bash
# Run V3 Final with optimal walk-forward-validated parameters
cd "$(dirname "$0")/../python"
if [ ! -f ../results/trades_full.csv ]; then
    echo "ERROR: Run scripts/full_pipeline.sh first to generate trade log"
    exit 1
fi
python v3_final.py ../results/trades_full.csv
