"""CLI entry for /superskillret:remove <name>.

Mirrors the pattern of add_skill_cli.py: __file__-based ROOT, sibling
scripts on sys.path, then daemon_client.daemon_request.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from daemon_client import daemon_request  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Remove a user-added superskillret skill by name."
    )
    ap.add_argument("name", help="kebab-case skill name from frontmatter")
    args = ap.parse_args()

    name = args.name.strip()
    if not name:
        print("FAIL: empty name")
        return 1

    try:
        reply = daemon_request({"op": "remove_user_skill", "name": name})
    except Exception as e:
        print(f"FAIL: {e}")
        return 1

    if reply.get("ok"):
        row = reply.get("removed_row")
        print(f"removed '{name}' (was at user-pool row {row})")
        return 0

    print(f"FAIL: {reply.get('error', reply)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
