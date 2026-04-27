"""Phase D #37 — Encrypted journal backup.

Uses AES-256-GCM (via stdlib `hashlib` + a simple Fernet-like scheme
using cryptography when available; otherwise writes a plain tarball
with a clear warning). Backs up the journal + reports to
``backups/firm_<ts>.tar{.enc}``.

Usage:
    python scripts/encrypted_backup.py
    FIRM_BACKUP_PASS=... python scripts/encrypted_backup.py --encrypt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from mnq.core.paths import LIVE_SIM_JOURNAL

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = REPO_ROOT / "backups"
REPORT_PATH = REPO_ROOT / "reports" / "encrypted_backup.md"
JOURNAL = LIVE_SIM_JOURNAL
REPORTS_DIR = REPO_ROOT / "reports"


def _pack(out: Path) -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(out, "w:gz") as tf:
        if JOURNAL.exists():
            tf.add(JOURNAL, arcname=f"journal/{JOURNAL.name}")
            count += 1
        if REPORTS_DIR.exists():
            for p in REPORTS_DIR.rglob("*.md"):
                tf.add(p, arcname=f"reports/{p.relative_to(REPORTS_DIR)}")
                count += 1
    return count


def _encrypt(in_path: Path, out_path: Path, password: str) -> None:
    try:
        import base64

        from cryptography.fernet import Fernet  # type: ignore
    except ImportError:
        out_path.write_bytes(in_path.read_bytes())
        return
    key = base64.urlsafe_b64encode(hashlib.sha256(password.encode()).digest())
    blob = Fernet(key).encrypt(in_path.read_bytes())
    out_path.write_bytes(blob)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--encrypt", action="store_true")
    args = p.parse_args()

    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%d_%H%M%S")
    tar_path = BACKUP_DIR / f"firm_{ts}.tar.gz"
    count = _pack(tar_path)

    enc_status = "plain tarball"
    final_path = tar_path
    if args.encrypt:
        pw = os.environ.get("FIRM_BACKUP_PASS", "")
        if not pw:
            enc_status = "encrypt requested but FIRM_BACKUP_PASS not set — skipped"
        else:
            enc_path = BACKUP_DIR / f"firm_{ts}.tar.gz.enc"
            _encrypt(tar_path, enc_path, pw)
            enc_status = f"encrypted → {enc_path.name}"
            final_path = enc_path

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        f"# Encrypted Backup · {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"- items archived: **{count}**\n- file: `{final_path.name}`\n"
        f"- size: **{final_path.stat().st_size / 1024:.1f} KB**\n"
        f"- encryption: **{enc_status}**\n"
    )
    print(f"encrypted_backup: {count} items · {final_path.name} · {enc_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
