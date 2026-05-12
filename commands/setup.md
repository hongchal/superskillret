---
name: setup
description: Bring superskillret to a ready state synchronously — authorizes the plugin under Claude Code auto-mode (idempotent settings.json edit), runs install.sh if needed (~1-2 min on first time), spawns the daemon, and verifies retrieval end-to-end. Use right after /plugin install superskillret@thakicloud so you don't have to wait through the lazy-fork on the first 1-2 user prompts. Idempotent — re-running on an already-ready plugin reports each phase as "already ready" without side effects.
disable-model-invocation: true
---

## superskillret :: synchronous setup

```!
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_cli.py"
```

Surface the final "✓ Ready" line and the quick-reference block to the user. If a phase failed, restate which phase and which log to inspect.

Notes on the one-liner:
- Claude Code resolves `${CLAUDE_PLUGIN_ROOT}` to an absolute path before evaluating the bash body, so the static shell-permission check sees a fully literal command (no `$(...)` subshells, no unresolved `${...}`) and lets it through without needing a custom `Bash(...)` allow rule.
- `setup_cli.py` self-resolves its plugin root via `__file__` and falls back to `CLAUDE_SKILL_DIR` internally if needed; we don't do that resolution in shell because conditional bash with subshells trips the same static check.
- `python3` is the system python — `setup_cli.py` only uses stdlib until it invokes `scripts/install.sh`, at which point the plugin's own venv takes over.
