#!/bin/bash
# Apex V3 Framework one-command setup
set -e
echo "════════════════════════════════════════════════════════════"
echo "  APEX V3 FRAMEWORK - SETUP"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Installing Python dependencies..."
pip install -r requirements.txt --quiet
echo "  ✓ Dependencies installed"
echo ""
if [ -z "$DATABENTO_API_KEY" ]; then
    echo "⚠️  DATABENTO_API_KEY not set"
    echo "    Get a free key at https://databento.com (signup → portal → API Keys)"
    echo "    Then run: export DATABENTO_API_KEY=\"db-your-key-here\""
    echo ""
fi
mkdir -p data results
echo "  ✓ Created data/ and results/ directories"
echo ""
echo "Verifying installation with sample data..."
cd python/
python -c "from confluence_scorer import score_signal; from v3_final import classify_by_calibrated_score; print('  ✓ Core modules import OK')"
cd ..
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  SETUP COMPLETE"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Set your Databento key: export DATABENTO_API_KEY=\"db-xxx\""
echo "  2. Run the full pipeline: bash scripts/full_pipeline.sh"
echo "  3. Or read QUICKSTART.md for step-by-step instructions"
