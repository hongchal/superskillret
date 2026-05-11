"""CLI entry point for /superskillret:add.

Reads a SKILL.md path (or '-' for stdin), validates it locally, then asks the
daemon to encode + persist via the {"op": "add_skill", ...} protocol.

Exits 0 on success, 1 on validation/daemon error. Prints a human-readable
summary on stdout that the slash command surfaces to Claude.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from skill_validator import validate_skill_file, validate_skill_text


SOCKET_PATH = os.environ.get("SUPERSKILLRET_SOCKET", "/tmp/superskillret.sock")


def daemon_request(req: dict, timeout: float = 30.0) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET_PATH)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise RuntimeError(
            f"daemon not reachable at {SOCKET_PATH}; send any user prompt to "
            "Claude Code first (the UserPromptSubmit hook will lazy-spawn it) "
            f"or run scripts/install.sh if not yet installed [{e}]"
        )
    try:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while True:
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        return json.loads(buf.decode("utf-8"))
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Add a SKILL.md to the superskillret retrieval index."
    )
    ap.add_argument(
        "path",
        help="Path to SKILL.md (use '-' to read from stdin)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing skill with the same name",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    args = ap.parse_args()

    # 1. Local validation
    if args.path == "-":
        text = sys.stdin.read()
        result = validate_skill_text(text, source="<stdin>")
    else:
        result = validate_skill_file(Path(args.path).expanduser())

    if not result.valid:
        if args.json:
            print(json.dumps({"ok": False, "stage": "validation",
                              "errors": result.errors,
                              "warnings": result.warnings}, indent=2))
        else:
            print(f"✗ Validation failed for {args.path}")
            for e in result.errors:
                print(f"    ERR: {e}")
            for w in result.warnings:
                print(f"    WARN: {w}")
        return 1

    # 2. Daemon round-trip
    try:
        reply = daemon_request({
            "op": "add_skill",
            "name": result.name,
            "description": result.description,
            "body": result.body,
            "force": args.force,
            "source": str(Path(args.path).expanduser())
                if args.path != "-" else "<stdin>",
        }, timeout=60.0)
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "stage": "daemon",
                              "error": str(e)}, indent=2))
        else:
            print(f"✗ Daemon error: {e}")
        return 1

    if not reply.get("ok"):
        if args.json:
            print(json.dumps({"ok": False, "stage": "daemon", **reply},
                             indent=2))
        else:
            print(f"✗ Daemon refused: {reply.get('error', reply)}")
        return 1

    # 3. Human summary
    if args.json:
        out = {
            "ok": True,
            "name": result.name,
            "warnings": result.warnings,
            **reply,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print(f"✓ Added '{result.name}' to superskillret")
    print(f"    user-pool row: {reply.get('user_row_index')}")
    print(f"    global row:    {reply.get('global_row_index')}")
    print(f"    encode time:   {reply.get('encode_ms', 0):.0f} ms")

    quality = reply.get("quality") or {}
    near_name = quality.get("nearest_existing_name")
    near_sim = quality.get("nearest_existing_score")
    if near_name is not None:
        flag = " ← near-duplicate, consider editing instead" \
            if (near_sim or 0) > 0.85 else ""
        print(f"    nearest existing: {near_name} ({near_sim:.3f}){flag}")
    self_score = quality.get("self_retrieval_score")
    if self_score is not None:
        flag = " ← description may be too generic" if self_score < 0.6 else ""
        print(f"    self-retrieval:   {self_score:.3f}{flag}")

    for w in result.warnings:
        print(f"    WARN: {w}")
    for w in reply.get("warnings", []):
        print(f"    WARN: {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
