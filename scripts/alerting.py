"""Phase A #01 + #06 — Discord/Slack webhook dispatcher with priority tiers.

Reads the most recent N trades from the journal and posts a formatted
message to a configured webhook URL. Supports three priority tiers:

* info   — routine status (daily rollup, quick stats)
* warn   — degraded state (drawdown threshold, edge decay, streak)
* action — intervention required (kill switch, hard loss cap, heartbeat loss)

Tiers map to different channels or role-mentions via env vars:

    FIRM_WEBHOOK_INFO
    FIRM_WEBHOOK_WARN
    FIRM_WEBHOOK_ACTION

Dry-run by default — prints the JSON it *would* send. Pass --live to
actually POST (requires one of the above env vars to be set).

Usage:
    python scripts/alerting.py --tier info
    python scripts/alerting.py --tier action --live
    python scripts/alerting.py --message "Kill switch tripped" --tier action
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "alerting.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _trade_utils import load_trades, summary_stats  # noqa: E402


TIER_ENV = {
    "info":   "FIRM_WEBHOOK_INFO",
    "warn":   "FIRM_WEBHOOK_WARN",
    "action": "FIRM_WEBHOOK_ACTION",
}
TIER_PRIORITY = {"info": 0, "warn": 1, "action": 2}


@dataclass
class AlertPayload:
    tier: str
    message: str
    stats: dict
    ts: str

    def to_discord(self) -> dict:
        color = {"info": 0x22c55e, "warn": 0xf59e0b, "action": 0xef4444}[self.tier]
        tiny = f"n={self.stats['n']} · WR={self.stats['win_rate']:.1%} · PF={self.stats['profit_factor']:.2f}"
        return {
            "embeds": [{
                "title": f"[{self.tier.upper()}] {self.message}",
                "description": f"`{tiny}` — total PnL `${self.stats['total_pnl']:+.2f}`",
                "color": color,
                "timestamp": self.ts,
                "footer": {"text": "The Firm · alerting.py"},
            }]
        }

    def to_slack(self) -> dict:
        emoji = {"info": ":information_source:", "warn": ":warning:", "action": ":rotating_light:"}[self.tier]
        return {
            "text": f"{emoji} *{self.tier.upper()}* · {self.message}",
            "attachments": [{
                "color": {"info": "good", "warn": "warning", "action": "danger"}[self.tier],
                "fields": [
                    {"title": "Trades", "value": f"{self.stats['n']}", "short": True},
                    {"title": "WR", "value": f"{self.stats['win_rate']:.1%}", "short": True},
                    {"title": "PF", "value": f"{self.stats['profit_factor']:.2f}", "short": True},
                    {"title": "PnL", "value": f"${self.stats['total_pnl']:+.2f}", "short": True},
                ],
                "ts": int(datetime.now(UTC).timestamp()),
            }]
        }


def _post(url: str, body: dict) -> tuple[int, str]:
    req = urlrequest.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", errors="replace")[:200]
    except Exception as exc:  # noqa: BLE001
        return 0, f"error: {exc}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", choices=list(TIER_ENV), default="info")
    p.add_argument("--message", default="")
    p.add_argument("--live", action="store_true", help="POST to webhook URL")
    p.add_argument("--format", choices=["discord", "slack"], default="discord")
    args = p.parse_args()

    trades = load_trades()
    stats = summary_stats(trades)
    msg = args.message or (
        f"Auto-rollup · {stats['n']} trades · ${stats['total_pnl']:+.2f}"
    )
    alert = AlertPayload(
        tier=args.tier, message=msg, stats=stats,
        ts=datetime.now(UTC).isoformat(),
    )
    body = alert.to_discord() if args.format == "discord" else alert.to_slack()
    print(json.dumps(body, indent=2))

    status_line = "dry-run (set --live + env var to POST)"
    if args.live:
        env_name = TIER_ENV[args.tier]
        url = os.environ.get(env_name, "").strip()
        if not url:
            print(f"[alerting] no URL in ${env_name}; cannot post live", file=sys.stderr)
            return 2
        code, body_back = _post(url, body)
        status_line = f"POST status={code} body_back={body_back}"
        print(status_line)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        f"# Alerting · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- tier: **{args.tier}**\n- message: {msg}\n- trades: {stats['n']}\n"
        f"- win rate: {stats['win_rate']:.1%}\n- PnL: ${stats['total_pnl']:+.2f}\n\n"
        f"## Transport\n`{status_line}`\n\n## Payload\n```json\n{json.dumps(body, indent=2)}\n```\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
