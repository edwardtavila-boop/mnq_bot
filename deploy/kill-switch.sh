#!/bin/bash
# kill-switch.sh — Emergency operator script to halt all trading immediately
#
# This script arms the kill-switch file that the mnq_bot executor monitors.
# Once armed, the CircuitBreaker in executor/safety.py will reject all new
# entry orders, though it may take up to 2 seconds to take effect (TTL on
# the kill-switch file check).
#
# Usage:
#   sudo ./deploy/kill-switch.sh
#
# To disarm (resume trading after inspection):
#   rm /var/lib/mnq-bot/HALT
#   systemctl restart mnq-bot

set -e

HALT_FILE="/var/lib/mnq-bot/HALT"

echo "=== MNQ Bot Kill Switch ==="
echo "Arming kill-switch file: $HALT_FILE"

# Ensure the directory exists
mkdir -p "$(dirname "$HALT_FILE")"

# Touch the file to arm the kill switch
touch "$HALT_FILE"

echo "✓ Kill switch armed."
echo ""
echo "Trading will be halted within 2 seconds."
echo ""
echo "To verify the kill switch is active:"
echo "  systemctl status mnq-bot"
echo "  tail -f /var/log/mnq-bot/*.log"
echo ""
echo "To resume trading after inspection:"
echo "  rm $HALT_FILE"
echo "  systemctl restart mnq-bot"
