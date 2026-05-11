---
name: add
description: Add a SKILL.md file (or a directory of them) to the superskillret retrieval index. Validates frontmatter (name, description, body) and security before encoding; appends to a writable user-pool that survives system index upgrades. Use when the user wants to register a custom skill. Accepts a file path, a directory of *.md files, a bare skill name (looked up under ~/.superskillret/skills/), or no argument at all (scans that default directory).
disable-model-invocation: true
argument-hint: <path | name | directory>
---

## Add SKILL.md(s) to superskillret

Resolution order (handled inside `add_skill_cli.py`):

- no argument → scans `~/.superskillret/skills/*.md` and `*/SKILL.md`
- bare name → looks for `<DEFAULT>/<name>/SKILL.md` or `<DEFAULT>/<name>.md`
- file path → adds that file
- directory path → batch-adds every `*.md` and `*/SKILL.md` inside
- `-` → reads SKILL.md content from stdin

Override the default location with `SUPERSKILLRET_USER_SKILLS_DIR`.

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

PY="$PLUGIN_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

eval "$PY \"$PLUGIN_ROOT/scripts/add_skill_cli.py\" $ARGUMENTS"
```

Report the result back to the user:

- On success, surface the per-skill quality numbers the CLI prints (`self-retrieval`, `nearest existing`) and the batch totals (added / skipped / failed).
- On validation failure, restate which rules tripped and how to fix the SKILL.md.
- On `○ Skipped — already in user pool`, mention that re-adding requires `--force`.
