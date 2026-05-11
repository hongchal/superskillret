"""CLI entry for /superskillret:list.

Mirrors the pattern of add_skill_cli.py: resolves ROOT from __file__, puts
sibling scripts on sys.path, then calls daemon_client.daemon_request.
This avoids the env-var-handoff fragility of inline heredoc Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from daemon_client import daemon_request  # noqa: E402


def main() -> int:
    try:
        reply = daemon_request({"op": "list_user_skills"})
    except Exception as e:
        print(f"FAIL: {e}")
        return 1

    skills = reply.get("skills", [])
    if not skills:
        print("no user-added skills yet — use /superskillret:add <path> to register one")
        return 0

    print(f"{len(skills)} user-added skill(s):")
    print()
    for s in skills:
        name = s.get("name", "?")
        desc = (s.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 80:
            desc = desc[:77] + "..."
        body_kb = len((s.get("body") or "").encode("utf-8")) / 1024
        gr = s.get("global_row")
        print(f"  - {name}  [global row {gr}]")
        print(f"      {desc}")
        print(f"      (body: {body_kb:.1f} KB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
