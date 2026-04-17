# Deployment Guide — EVOLUTIONARY TRADING ALGO

This directory contains production deployment artifacts for the EVOLUTIONARY TRADING ALGO trading system.

## Quick Start

### Building the Docker Image

```bash
# Build the image locally
docker build -t mnq-bot:latest .

# Or with a specific version tag
docker build -t mnq-bot:0.0.1 --build-arg VERSION=0.0.1 .
```

### Running Locally (Docker)

```bash
# Run the health check (default)
docker run --rm mnq-bot:latest

# Run with custom environment variables
docker run --rm \
  --env-file .env \
  -v /var/lib/mnq-bot:/var/lib/mnq-bot \
  mnq-bot:latest doctor

# Run the actual bot (once executor is wired)
# docker run -d \
#   --env-file .env \
#   -v /var/lib/mnq-bot:/var/lib/mnq-bot \
#   -v /var/log/mnq-bot:/var/log/mnq-bot \
#   -p 9108:9108 \
#   --name mnq-bot \
#   mnq-bot:latest run
```

### Environment Variables

Create `/etc/mnq-bot/env` with the following variables:

```bash
# Tradovate credentials (from .env.example)
TV_USERNAME=<your_tradovate_username>
TV_PASSWORD=<your_tradovate_password>
TV_APP_ID=<your_app_id>
TV_APP_VERSION=<your_app_version>
TV_DEVICE_ID=<your_device_id>
TV_CID=<your_cid>
TV_SEC=<your_sec>

# Tradovate environment (demo or live)
TV_ENV=demo

# Account ID for trading
TV_ACCOUNT_ID=<your_account_id>

# Optional: Bot runtime settings
PYTHONUNBUFFERED=1
```

Run `mnq venue tradovate auth-test` to verify credentials before deployment.

### Systemd Installation

```bash
# Copy systemd unit files
sudo cp deploy/systemd/mnq-bot.service /etc/systemd/system/
sudo cp deploy/systemd/mnq-bot.timer /etc/systemd/system/

# Create application directory
sudo mkdir -p /opt/mnq-bot /var/lib/mnq-bot /var/log/mnq-bot
sudo chown mnq:mnq /opt/mnq-bot /var/lib/mnq-bot /var/log/mnq-bot

# Create mnq user (if not exists)
sudo useradd -r -u 10001 -s /bin/false mnq || true

# Copy application files
sudo cp -r src /opt/mnq-bot/
sudo cp -r docs /opt/mnq-bot/

# Set proper permissions
sudo chown -R mnq:mnq /opt/mnq-bot
sudo chmod 750 /opt/mnq-bot

# Reload systemd
sudo systemctl daemon-reload

# Start the timer (health checks every 5 minutes)
sudo systemctl enable --now mnq-bot.timer

# Or start the main service
# sudo systemctl enable --now mnq-bot.service
```

### Verifying Deployment

```bash
# Check systemd unit status
sudo systemctl status mnq-bot.timer
sudo systemctl status mnq-bot.service

# View logs
sudo journalctl -u mnq-bot.service -f

# Check Prometheus metrics endpoint (port 9108)
curl http://localhost:9108/metrics

# Run health check manually
sudo systemctl start mnq-bot.service
```

## Configuration

### Kill Switch

The bot monitors `/var/lib/mnq-bot/HALT` for an emergency halt file. To emergency-stop trading:

```bash
sudo /opt/mnq-bot/deploy/kill-switch.sh
```

To resume:

```bash
sudo rm /var/lib/mnq-bot/HALT
sudo systemctl restart mnq-bot.service
```

### Event Journal and State Rebuild

The bot maintains an event journal at `/var/lib/mnq-bot/events.sqlite`. To rebuild bot state after a crash:

```bash
# TODO: mnq replay --from-journal
# (Blocked on executor work — state machine not yet exposed in CLI)
```

## Monitoring

### Health Checks

The service includes automated health checks via `mnq doctor --json` every 5 minutes (via the timer unit).

### Prometheus Metrics

The bot exports metrics on port 9108. Key metrics to monitor:

- `orders_rejected_total` — Orders rejected by safety checks
- `ws_reconnects_total` — WebSocket reconnection attempts
- `safety_decisions_total{allowed="false"}` — Safety circuit breaker triggers
- `reconcile_diffs_total` — Order reconciliation mismatches

## Troubleshooting

- **Bot fails to start**: Check `/etc/mnq-bot/env` for missing or invalid credentials
- **Kill switch won't disarm**: Ensure `/var/lib/mnq-bot/HALT` is deleted before restarting
- **Prometheus metrics unavailable**: Verify port 9108 is not blocked by firewall
- **State corrupt after crash**: Replay from the event journal (see RUNBOOK.md)

See `docs/RUNBOOK.md` for detailed operational procedures.
