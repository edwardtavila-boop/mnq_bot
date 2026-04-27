"""One-shot helper to regenerate eta_v3_framework/v1_locked/.frozen_hashes.json
after a v1 re-freeze.

Run from anywhere: python scripts/regen_v1_locked_manifest.py
"""
import hashlib
import json
import pathlib

root = pathlib.Path(__file__).resolve().parents[1] / "eta_v3_framework" / "v1_locked"
hashes = {
    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(root.glob("*"))
    if p.is_file() and p.name != ".frozen_hashes.json"
}
manifest = {
    "_comment": (
        "SHA-256 of every file under eta_v3_framework/v1_locked/. v1 is intentionally "
        "frozen for reproducibility (see LOCKED.txt). The pytest gate at "
        "tests/level_1_unit/test_v1_locked_frozen.py asserts every file's hash "
        "matches this manifest. Regenerate ONLY when LOCKED.txt is moved/replaced "
        "and a new freeze is intended."
    ),
    "_regen_command": "python scripts/regen_v1_locked_manifest.py",
}
manifest.update(hashes)

out = root / ".frozen_hashes.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out}")
print(json.dumps(manifest, indent=2))
