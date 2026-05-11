---
name: setup
description: Bring superskillret to a ready state synchronously — runs install.sh if needed (~1-2 min on first time), spawns the daemon, and verifies retrieval end-to-end. Use right after /plugin install superskillret@hongchal so you don't have to wait through the lazy-fork on the first 1-2 user prompts. Idempotent — re-running on an already-ready plugin reports "Already ready" without side effects.
disable-model-invocation: true
---

## superskillret :: synchronous setup

```!
if [ -n "${CLAUDE_PLUGIN_ROOT}" ]; then
  PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
elif [ -n "${CLAUDE_SKILL_DIR}" ]; then
  PLUGIN_ROOT="$(dirname "${CLAUDE_SKILL_DIR}")"
else
  echo "FAIL: cannot resolve plugin root — neither CLAUDE_PLUGIN_ROOT nor CLAUDE_SKILL_DIR is set"
  exit 1
fi

if [ ! -d "$PLUGIN_ROOT/scripts" ]; then
  echo "FAIL: $PLUGIN_ROOT/scripts not found"
  exit 1
fi

# setup_cli.py uses only stdlib until install.sh finishes and the venv
# python is needed for the daemon — so the system python3 is fine here.
PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "FAIL: python3 not on PATH"
  exit 1
fi

"$PY" "$PLUGIN_ROOT/scripts/setup_cli.py"
```

Surface the final "✓ Ready" line and the quick-reference block to the user. If a phase failed, restate which phase and which log to inspect.
