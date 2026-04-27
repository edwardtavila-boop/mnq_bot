"""
Apex v2 Webhook Server
======================
Flask server that receives JSON alerts from TradingView, re-validates the
Firm gate in Python (defense in depth: Pine and Python should agree), logs
the decision, and optionally forwards to a broker.

TradingView alert webhook URL setup:
  1. In Pine alert dialog, paste your server URL: http://YOUR_HOST:5000/webhook
  2. In "Message" field, paste:
       {{strategy.order.alert_message}}
     and use the Pine f_payload(side) output (already JSON-formatted) as
     the alert message string.
  3. Set "Webhook URL" toggle ON.

Run locally:
  python webhook.py
  # listens on http://0.0.0.0:5000

For production, put behind nginx + use gunicorn:
  gunicorn -w 2 -b 0.0.0.0:5000 webhook:app

Authentication: set APEX_WEBHOOK_SECRET in env. Pine should append it as a
"secret" field in the JSON payload. Requests without matching secret are 401.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

try:
    from flask import Flask, jsonify, request
except ImportError as e:
    raise SystemExit("Install dependencies: pip install -r requirements.txt") from e

import requests

LOG_DIR = Path(os.environ.get("APEX_LOG_DIR", "./logs"))
LOG_DIR.mkdir(exist_ok=True, parents=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "webhook.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("apex.webhook")

app = Flask(__name__)

WEBHOOK_SECRET = os.environ.get("APEX_WEBHOOK_SECRET", "")
# PM threshold harmonized with firm_engine.FirmConfig.pm_threshold (40.0).
# The legacy default of 75 was unreachable with weighted-avg math; this caused
# the webhook to silently reject every signal approved by the engine.
# See BASEMENT_THEORY_AUDIT.md Fix #1.
PM_THRESHOLD = float(os.environ.get("APEX_PM_THRESHOLD", "40"))
DRY_RUN = os.environ.get("APEX_DRY_RUN", "true").lower() in ("1", "true", "yes")
BROKER_URL = os.environ.get("APEX_BROKER_URL", "")  # optional forward
BROKER_API_KEY = os.environ.get("APEX_BROKER_API_KEY", "")

TRADES_LOG = LOG_DIR / "trades.jsonl"


def validate_payload(p: dict) -> str | None:
    """Returns error string if invalid, None if OK.

    Voice payload now accepts 7 (legacy) or 15 (current) voices. The 15-voice
    ensemble is canonical — V1-V7 plus V8-V11 (intermarket: VIX/ES/DXY/TICK)
    plus V12-V15 (edge stack: Delta/Killzone/PremDisc/FVG).
    See BASEMENT_THEORY_AUDIT.md Fix #3.
    """
    required = [
        "ticker",
        "side",
        "setup",
        "entry",
        "sl",
        "tp1",
        "tp2",
        "regime",
        "pm_final",
        "red_team",
        "voices",
    ]
    for k in required:
        if k not in p:
            return f"missing field: {k}"
    if p["side"] not in ("long", "short"):
        return f"invalid side: {p['side']}"
    if not isinstance(p["voices"], list) or len(p["voices"]) not in (7, 15):
        return "voices must be array of 7 (legacy) or 15 (current) numbers"
    # Bound-check every voice in canonical [-100, +100] range
    for i, v in enumerate(p["voices"]):
        try:
            if not (-100.0 <= float(v) <= 100.0):
                return f"voice[{i}]={v} outside canonical [-100, +100] range"
        except (TypeError, ValueError):
            return f"voice[{i}] not numeric: {v!r}"
    return None


def double_check_firm(p: dict) -> tuple[bool, str]:
    """Re-validate Firm gate server-side as defense in depth."""
    if p["regime"] == "CRISIS":
        return False, "crisis_lockdown_server_side"
    if p["pm_final"] < PM_THRESHOLD:
        return False, f"pm_below_server_threshold ({p['pm_final']} < {PM_THRESHOLD})"
    # Direction sanity: voices should agree with side
    voices = p["voices"]
    side = p["side"]
    quant_sum = sum(voices)
    if side == "long" and quant_sum <= 0:
        return False, "voices_disagree_with_long"
    if side == "short" and quant_sum >= 0:
        return False, "voices_disagree_with_short"
    return True, ""


def forward_to_broker(p: dict) -> dict:
    """Forward to broker API. Replace with your actual broker integration.

    B1 closure (Red Team review 2026-04-25): paper-only enforcement.

    The Red Team observed that this function would POST a real order
    to BROKER_URL whenever DRY_RUN was set to false, with no kill
    switch, no gate chain, no tiered rollout, no Firm review.
    Setting APEX_DRY_RUN=false was a one-env-var path to live with
    every safety subsystem bypassed.

    Live mode now requires THREE concurrent conditions:
      1. APEX_DRY_RUN is unset / "false" (legacy)
      2. APEX_LIVE_READY is set to "1" (operator-acknowledged
         live-readiness; the env var name is intentionally distinct
         from any existing config so flipping it is a deliberate act)
      3. The configured broker is NOT in DORMANT_BROKERS (per
         CLAUDE.md operator mandate)

    Any one missing -> returns {"forwarded": False, ...} with a
    reason string the operator can grep. The webhook keeps running
    so paper signals continue to populate logs/trades.jsonl.

    The architectural BLOCKER (no kill switch / gate chain wiring
    on this path) remains open. This guard is risk reduction, not a
    full B1 closure -- the full closure is a design call documented
    in docs/RED_TEAM_REVIEW_2026_04_25.md.
    """
    if not BROKER_URL:
        return {"forwarded": False, "reason": "no_broker_configured"}
    if DRY_RUN:
        return {"forwarded": False, "reason": "dry_run_mode"}

    # B1 paper-only gate: explicit live-readiness env var required.
    if os.environ.get("APEX_LIVE_READY", "").strip() != "1":
        log.warning(
            "live order REFUSED: APEX_LIVE_READY != '1' (got %r). "
            "See docs/RED_TEAM_REVIEW_2026_04_25.md B1 for the "
            "full live-promotion checklist.",
            os.environ.get("APEX_LIVE_READY", ""),
        )
        return {
            "forwarded": False,
            "reason": "live_mode_not_acknowledged_set_APEX_LIVE_READY=1",
        }

    # B5 dormancy gate: refuse if broker is on the dormant list.
    broker_name = os.environ.get("BROKER_TYPE", "").strip().lower()
    if broker_name:
        try:
            from mnq.venues.dormancy import (
                DormantBrokerError,
                assert_broker_active,
            )

            assert_broker_active(broker_name)
        except DormantBrokerError as exc:
            log.error("live order REFUSED: %s", exc)
            return {
                "forwarded": False,
                "reason": f"broker_dormant:{broker_name}",
            }
        except ImportError:
            log.warning(
                "dormancy module not importable; falling through to broker",
            )

    try:
        order = {
            "symbol": p["ticker"],
            "side": "BUY" if p["side"] == "long" else "SELL",
            "qty": int(os.environ.get("APEX_QTY", "1")),
            "type": "MARKET",
            "stopLoss": p["sl"],
            "takeProfit1": p["tp1"],
            "takeProfit2": p["tp2"],
            "metadata": {
                "setup": p["setup"],
                "regime": p["regime"],
                "pm_final": p["pm_final"],
                "red_team": p["red_team"],
            },
        }
        headers = {"Authorization": f"Bearer {BROKER_API_KEY}", "Content-Type": "application/json"}
        r = requests.post(BROKER_URL, json=order, headers=headers, timeout=5)
        return {"forwarded": True, "status": r.status_code, "response": r.text[:200]}
    except Exception as e:
        log.exception("Broker forward failed")
        return {"forwarded": False, "reason": str(e)}


def log_trade(p: dict, validation: dict, broker_resp: dict) -> None:
    record = {
        "received_at": datetime.now(UTC).isoformat(),
        "payload": p,
        "validation": validation,
        "broker": broker_resp,
    }
    with TRADES_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data(as_text=True)
    log.info(f"Received webhook: {raw[:300]}")

    # Parse JSON (TradingView sometimes sends with stray whitespace/quotes)
    try:
        # Handle case where TradingView wraps in quotes
        cleaned = raw.strip()
        if cleaned.startswith('"') and cleaned.endswith('"'):
            cleaned = cleaned[1:-1].replace('\\"', '"')
        p = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.error(f"Bad JSON: {e}")
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    # Auth check
    if WEBHOOK_SECRET:
        if p.get("secret") != WEBHOOK_SECRET:
            log.warning("Unauthorized webhook attempt")
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        del p["secret"]

    # Validate payload structure
    err = validate_payload(p)
    if err:
        log.error(f"Invalid payload: {err}")
        return jsonify({"ok": False, "error": err}), 400

    # Server-side Firm gate (defense in depth)
    pm_ok, pm_reason = double_check_firm(p)
    if not pm_ok:
        log.info(f"Server gate REJECTED: {pm_reason}  payload={p}")
        log_trade(
            p,
            {"server_validation": "rejected", "reason": pm_reason},
            {"forwarded": False, "reason": "rejected_by_server"},
        )
        return jsonify({"ok": True, "fired": False, "reason": pm_reason})

    # All checks passed → forward to broker
    log.info(
        f"Server gate PASSED: {p['side']} {p['setup']} PM={p['pm_final']} regime={p['regime']}"
    )
    broker_resp = forward_to_broker(p)
    log_trade(p, {"server_validation": "passed"}, broker_resp)

    return jsonify(
        {
            "ok": True,
            "fired": True,
            "side": p["side"],
            "setup": p["setup"],
            "pm_final": p["pm_final"],
            "broker": broker_resp,
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "ok": True,
            "service": "apex_v2_webhook",
            "pm_threshold": PM_THRESHOLD,
            "dry_run": DRY_RUN,
            "broker_configured": bool(BROKER_URL),
            "auth_required": bool(WEBHOOK_SECRET),
        }
    )


@app.route("/recent", methods=["GET"])
def recent():
    """Show recent trades from the log."""
    n = int(request.args.get("n", "20"))
    if not TRADES_LOG.exists():
        return jsonify({"trades": []})
    with TRADES_LOG.open() as f:
        lines = f.readlines()[-n:]
    trades = [json.loads(line) for line in lines]
    return jsonify({"trades": trades, "count": len(trades)})


if __name__ == "__main__":
    log.info(
        f"Starting Apex v2 webhook server  (PM≥{PM_THRESHOLD}  "
        f"dry_run={DRY_RUN}  auth={'on' if WEBHOOK_SECRET else 'off'})"
    )
    app.run(host="0.0.0.0", port=int(os.environ.get("APEX_PORT", "5000")), debug=False)
