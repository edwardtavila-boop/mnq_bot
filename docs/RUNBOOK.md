# EVOLUTIONARY TRADING ALGO // Operator Runbook

This guide covers operational procedures for running EVOLUTIONARY TRADING ALGO in production: starting, stopping, emergency procedures, credential management, and troubleshooting.

## Table of Contents

1. [Starting and Stopping](#starting-and-stopping)
2. [The Kill Switch](#the-kill-switch)
3. [Flattening Positions](#flattening-positions)
4. [Rotating Credentials](#rotating-credentials)
5. [Rebuilding State from Journal](#rebuilding-state-from-journal)
6. [Monitoring](#monitoring)
7. [Common Failure Modes](#common-failure-modes)
8. [Emergency Contacts](#emergency-contacts)

---

## Starting and Stopping

### Start the Bot

```bash
# Start the main bot service
sudo systemctl start mnq-bot.service

# Or start the health-check timer (5-minute polling)
sudo systemctl start mnq-bot.timer

# Verify it's running
sudo systemctl status mnq-bot.service
```

### Stop the Bot

```bash
# Graceful stop (waits 30 seconds for shutdown)
sudo systemctl stop mnq-bot.service

# Hard stop (immediate)
sudo systemctl kill -s SIGKILL mnq-bot.service

# Stop the timer
sudo systemctl stop mnq-bot.timer
```

### View Logs

```bash
# Tail in real time
sudo journalctl -u mnq-bot.service -f

# Last 50 lines
sudo journalctl -u mnq-bot.service -n 50

# Since a specific time
sudo journalctl -u mnq-bot.service --since "2 hours ago"

# Errors only
sudo journalctl -u mnq-bot.service -p err
```

---

## The Kill Switch

The kill-switch is an emergency mechanism to halt trading **without restarting or deploying code**. The bot monitors `/var/lib/mnq-bot/HALT` and rejects all new entry orders if the file exists.

### How It Works

- **Check interval**: 2 seconds (cached to avoid FS hammering)
- **Effect on existing trades**: Kill switch halts *new entries* only; open positions are not automatically closed
- **Location**: `/var/lib/mnq-bot/HALT`
- **Code**: `src/mnq/executor/safety.py` — `KillSwitchFile` class

### Arm the Kill Switch (Emergency Stop)

```bash
# One-command emergency halt
sudo /opt/mnq-bot/deploy/kill-switch.sh

# Or manually:
sudo touch /var/lib/mnq-bot/HALT
```

**Effect**: Within 2 seconds, all new entry orders are rejected with reason `"kill_switch"`.

### Verify Kill Switch Is Armed

```bash
# Check the file exists
ls -la /var/lib/mnq-bot/HALT

# Check bot logs for rejection reason
sudo journalctl -u mnq-bot.service | grep kill_switch
```

### Disarm the Kill Switch (Resume Trading)

```bash
# Remove the file
sudo rm /var/lib/mnq-bot/HALT

# Restart the bot to clear any stale state
sudo systemctl restart mnq-bot.service

# Verify it's running normally
sudo systemctl status mnq-bot.service
```

---

## Flattening Positions

Flattening means closing all open positions. This may be needed for maintenance, emergency scenarios, or orderly shutdown.

### Flatten via Tradovate Web UI

⚠️ **Current method (manual)**: Log in to the Tradovate web dashboard and close all positions manually:

1. Open https://demo.tradovate.com (or live.tradovate.com)
2. Log in with the account credentials
3. Navigate to **Positions**
4. Close each open position

### Flatten via CLI (TODO)

⚠️ **TODO**: The `mnq venue flatten` CLI command does not yet exist. Once executor work is complete:

```bash
# Expected command (not yet implemented):
# mnq venue flatten --env demo --account-id <ACCOUNT_ID>
```

**Blocked on**: `src/mnq/cli/venue.py` needs a new `flatten` subcommand; Tradovate REST client needs a close-all-positions method.

### After Flattening

1. Verify no open positions in the Tradovate dashboard
2. Wait for any pending orders to fill
3. Cross-check bot logs for reconciliation messages:
   ```bash
   sudo journalctl -u mnq-bot.service | grep reconcile
   ```
4. If reconciliation diffs appear, investigate (see [Common Failure Modes](#common-failure-modes))

---

## Rotating Credentials

Credentials are loaded from `/etc/mnq-bot/env` at startup. To rotate (e.g., password change):

### Update the Credential File

```bash
# Edit the env file
sudo nano /etc/mnq-bot/env

# Example content:
# TV_USERNAME=user@example.com
# TV_PASSWORD=NewPassword123
# TV_APP_ID=abc123
# ...
```

### Verify New Credentials

```bash
# Test login without restarting the bot
mnq venue tradovate auth-test --env demo
```

If login fails, revert the change and investigate.

### Restart the Bot

```bash
sudo systemctl restart mnq-bot.service
sudo systemctl status mnq-bot.service
```

### Verify Successful Startup

```bash
sudo journalctl -u mnq-bot.service -n 20 --no-pager | grep -E "startup|auth|error"
```

---

## Rebuilding State from Journal

The bot maintains an event journal at `/var/lib/mnq-bot/events.sqlite`. If the bot crashes or state becomes inconsistent, replay the journal to reconstruct it.

### Locate the Journal

```bash
ls -lh /var/lib/mnq-bot/events.sqlite
```

### Replay the Journal (TODO)

⚠️ **TODO**: The `mnq replay` command does not yet exist. Once executor work is complete:

```bash
# Expected usage (not yet implemented):
# mnq storage replay --from-journal /var/lib/mnq-bot/events.sqlite --dry-run
# mnq storage replay --from-journal /var/lib/mnq-bot/events.sqlite
```

**Blocked on**:
- `src/mnq/storage/` package needs to expose a replay interface
- Order state machine must be wired into the CLI
- Crash recovery logic must be implemented in the executor startup path

### Until Replay is Available

1. **Stop the bot**:
   ```bash
   sudo systemctl stop mnq-bot.service
   ```

2. **Inspect the journal** (requires a custom script — not yet provided):
   ```bash
   # Example: list events in the journal
   sqlite3 /var/lib/mnq-bot/events.sqlite "SELECT * FROM events LIMIT 20;"
   ```

3. **Backup the journal**:
   ```bash
   sudo cp /var/lib/mnq-bot/events.sqlite /var/lib/mnq-bot/events.sqlite.backup.$(date +%s)
   ```

4. **Flatten all positions** (see [Flattening Positions](#flattening-positions)) to clean up state manually

5. **Delete the journal** (fresh start):
   ```bash
   sudo rm /var/lib/mnq-bot/events.sqlite
   ```

6. **Restart the bot**:
   ```bash
   sudo systemctl start mnq-bot.service
   ```

---

## Monitoring

### Health Checks

The bot runs automated health checks every 5 minutes via the systemd timer:

```bash
# View timer status
sudo systemctl status mnq-bot.timer

# Manually trigger a health check
mnq doctor
# or
mnq doctor --json  # Machine-readable output
```

Health check covers:
- Python version (>= 3.12)
- Required env vars (TV_*)
- Module imports
- Strategy spec loading
- Runtime dependencies

### Prometheus Metrics

The bot exports metrics on **port 9108** (http://localhost:9108/metrics).

#### Key Metrics to Monitor

| Metric | Type | Meaning |
|--------|------|---------|
| `orders_rejected_total` | Counter | Total orders rejected by safety checks |
| `ws_reconnects_total` | Counter | WebSocket reconnection attempts |
| `safety_decisions_total{allowed="false"}` | Counter | Safety circuit breaker rejections |
| `reconcile_diffs_total` | Counter | Order reconciliation mismatches |
| `session_pnl_usd` | Gauge | Current session P&L |
| `consecutive_losses` | Gauge | Current consecutive losing-trade streak |

#### Checking Metrics

```bash
# Get all metrics
curl -s http://localhost:9108/metrics

# Filter for order rejections
curl -s http://localhost:9108/metrics | grep orders_rejected

# Filter for safety decisions
curl -s http://localhost:9108/metrics | grep safety_decisions
```

### Alerting Suggestions

Set up alerts for:

1. **Service down**: `up{job="mnq-bot"} == 0`
2. **Kill switch armed**: `safety_decisions_total{allowed="false",reason="kill_switch"} > 0`
3. **High rejection rate**: `rate(orders_rejected_total[5m]) > 0.1`
4. **Consecutive losses at threshold**: `consecutive_losses >= 5`
5. **Reconciliation drift**: `reconcile_diffs_total > 0` (investigate immediately)

---

## Common Failure Modes

### 1. WebSocket Disconnection

**Symptom**: Logs show `ws_reconnects_total` increasing; bot logs "WS disconnected"

**Cause**: Network issue, Tradovate server issue, or session expiry

**Recovery**:
```bash
# Check network connectivity
ping api.tradovate.com

# Restart the bot
sudo systemctl restart mnq-bot.service

# Check if reconnect succeeds
sudo journalctl -u mnq-bot.service -f | grep -i "ws\|websocket"
```

### 2. Auth Session Limit Exceeded

**Symptom**: Logs show authentication failure; bot cannot place orders

**Cause**: Too many concurrent logins or session expired

**Recovery**:
1. Kill other browser sessions to the Tradovate dashboard
2. Rotate credentials (see [Rotating Credentials](#rotating-credentials))
3. Restart the bot

### 3. Kill Switch Triggered

**Symptom**: Logs show `reason="kill_switch"`; no new orders placed

**Cause**: The `/var/lib/mnq-bot/HALT` file exists (intentional or accidental)

**Recovery**:
```bash
sudo rm /var/lib/mnq-bot/HALT
sudo systemctl restart mnq-bot.service
```

### 4. Reconciliation Diff

**Symptom**: Logs show `reconcile_diffs_total` counter incrementing

**Cause**: Mismatch between bot's order state and Tradovate's actual orders

**Recovery** (escalation required):
1. **Flatten all positions immediately** to avoid further divergence
2. **Stop the bot**:
   ```bash
   sudo systemctl stop mnq-bot.service
   ```
3. **Investigate**: Compare bot logs with Tradovate dashboard
4. **Rebuild state**: Replay journal or manually correct state (see [Rebuilding State from Journal](#rebuilding-state-from-journal))
5. **Restart and monitor** carefully

### 5. Circuit Breaker Tripped

**Symptom**: Logs show `reason="consecutive_losses"` or `reason="daily_drawdown"`

**Cause**: Too many losing trades in a row (>= 5) or daily loss exceeds threshold (-$500)

**Recovery**:
1. Investigate the underlying trading losses (may indicate a bug or market condition)
2. **Do not** immediately disable the breaker — it exists for protection
3. **Reset the breaker** by restarting at a session boundary (market open):
   ```bash
   sudo systemctl restart mnq-bot.service
   ```
4. Monitor for repeated trips before resuming live trading

---

## Emergency Contacts

| Role | Contact | Escalation |
|------|---------|-----------|
| On-Call Operator | [TBD] | Primary responder for all runtime issues |
| Trading Systems Lead | [TBD] | Escalation for reconciliation diffs, state corruption |
| Venue Liaison | [TBD] | Contact for Tradovate auth or connectivity issues |

**Incident Severity**:
- **Critical**: Kill switch armed or positions underwater (call on-call immediately)
- **High**: Reconciliation diff detected (escalate within 5 minutes)
- **Medium**: WS disconnection or auth failure (restart and monitor; escalate if persists)
- **Low**: Health check warnings (investigate within 1 hour)

---

## Appendix: Useful Commands

```bash
# Quick status
sudo systemctl status mnq-bot.service mnq-bot.timer

# Restart everything
sudo systemctl restart mnq-bot.service mnq-bot.timer

# Clear journals
sudo journalctl --vacuum-time=7d

# Check open positions (from CLI, once flatten is available)
# mnq venue tradovate list-positions --env demo --account-id <ID>
```
