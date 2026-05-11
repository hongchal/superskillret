"""SKILL.md validator for user-supplied skill files.

Validates structure, fields, and content before a skill is added to the
superskillret retrieval index. Pure function with no side effects so it
can be unit-tested independently of the daemon.

Pipeline (block on first failure):
  1. File checks   — exists, readable, ≤ 1 MB, valid UTF-8
  2. Frontmatter   — '---' delimiters present and YAML-parseable
  3. Required      — 'name' and 'description' present and non-empty
  4. Field shape   — name kebab-case 2-64 chars, description 20-500 chars,
                     body ≥ 100 chars
  5. Security      — body must not contain known prompt-injection patterns
  6. Body size     — warning if body > 50 KB (extra input tokens per retrieve)

Quality checks (self-retrieval, near-duplicate) are out of scope here because
they need the embedding model and existing index; daemon.py runs them on the
already-validated parsed payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"disregard\s+(the\s+)?system\s+(prompt|instructions)", re.I),
    re.compile(r"</?\s*system[-_]?reminder\s*/?>", re.I),
    re.compile(r"<\|im_(start|end)\|>", re.I),
    re.compile(r"<\s*system\s*>", re.I),
]

MAX_FILE_BYTES = 1_000_000
MIN_DESCRIPTION = 20
MAX_DESCRIPTION = 500
MIN_BODY = 100
WARN_BODY_KB = 50


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    name: Optional[str] = None
    description: Optional[str] = None
    body: Optional[str] = None
    raw_frontmatter: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "name": self.name,
            "description": self.description,
            "body": self.body,
        }


def _fail(*errors: str) -> ValidationResult:
    return ValidationResult(valid=False, errors=list(errors))


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split SKILL.md text into (frontmatter dict, body string).

    Accepts only the standard '---\\n...\\n---\\n<body>' shape. PyYAML is used
    when importable; otherwise a minimal key:value parser handles the simple
    'name: ...' / 'description: ...' frontmatter superskillret needs.
    """
    if not text.startswith("---"):
        raise ValueError("missing opening '---' frontmatter delimiter")
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        raise ValueError("missing closing '---' frontmatter delimiter")
    fm_text = rest[:end]
    body = rest[end + 4 :]
    body = body.lstrip("\r\n")

    fm = _load_yaml(fm_text)
    if not isinstance(fm, dict):
        raise ValueError("frontmatter must be a YAML mapping (got "
                         f"{type(fm).__name__})")
    return fm, body


def _load_yaml(fm_text: str) -> dict:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(fm_text) or {}
    except ImportError:
        # Minimal fallback: top-level "key: value" lines only. Sufficient for
        # SKILL.md frontmatter which has just name / description / optional
        # short scalars. Multi-line scalars or nesting fall back to a single
        # joined string.
        result: dict = {}
        current_key: Optional[str] = None
        for raw in fm_text.splitlines():
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith((" ", "\t")) and current_key:
                # continuation line for previous key
                result[current_key] = (
                    f"{result[current_key]} {line.strip()}".strip()
                )
                continue
            if ":" not in line:
                raise ValueError(
                    f"frontmatter line missing ':' separator: {line!r}"
                )
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            result[key] = value
            current_key = key
        return result


def validate_skill_file(file_path: Path) -> ValidationResult:
    """Validate a SKILL.md file. Returns ValidationResult (no exceptions)."""
    p = Path(file_path)

    if not p.exists():
        return _fail(f"file not found: {p}")
    if not p.is_file():
        return _fail(f"not a regular file: {p}")

    size = p.stat().st_size
    if size == 0:
        return _fail(f"file is empty: {p}")
    if size > MAX_FILE_BYTES:
        return _fail(
            f"file too large ({size} bytes > {MAX_FILE_BYTES} byte cap)"
        )

    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return _fail(f"file is not valid UTF-8: {e}")

    return validate_skill_text(text, source=str(p))


def validate_skill_text(text: str, source: str = "<inline>") -> ValidationResult:
    """Validate raw SKILL.md text. Useful for stdin / inline input."""

    try:
        fm, body = _parse_frontmatter(text)
    except ValueError as e:
        return _fail(f"frontmatter parse error: {e}")

    errors: list[str] = []
    warnings: list[str] = []

    raw_name = fm.get("name")
    raw_description = fm.get("description")

    name = str(raw_name).strip() if raw_name is not None else ""
    description = (
        str(raw_description).strip() if raw_description is not None else ""
    )

    if not name:
        errors.append("missing required frontmatter field 'name'")
    if not description:
        errors.append("missing required frontmatter field 'description'")

    if errors:
        return ValidationResult(valid=False, errors=errors)

    if not NAME_PATTERN.match(name):
        errors.append(
            f"name={name!r} must match {NAME_PATTERN.pattern} — lowercase "
            "letters/digits/hyphens only, 2-64 chars, starting with a "
            "letter or digit"
        )

    dlen = len(description)
    if dlen < MIN_DESCRIPTION:
        errors.append(
            f"description too short ({dlen} chars < {MIN_DESCRIPTION}); a "
            "vague description hurts retrieval recall"
        )
    if dlen > MAX_DESCRIPTION:
        errors.append(
            f"description too long ({dlen} chars > {MAX_DESCRIPTION}); long "
            "descriptions dilute the embedding signal — put detail in body"
        )

    body_stripped = body.strip()
    if len(body_stripped) < MIN_BODY:
        errors.append(
            f"body too short ({len(body_stripped)} chars < {MIN_BODY}); a "
            "skill without substantive content has no effect on Claude"
        )

    body_kb = len(body.encode("utf-8")) / 1024
    if body_kb > WARN_BODY_KB:
        warnings.append(
            f"body is {body_kb:.1f} KB; this many tokens will be injected on "
            f"every retrieve — consider trimming below {WARN_BODY_KB} KB"
        )

    for pat in INJECTION_PATTERNS:
        m = pat.search(body)
        if m:
            errors.append(
                f"possible prompt-injection pattern in body: {m.group(0)!r}"
            )

    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    return ValidationResult(
        valid=True,
        errors=[],
        warnings=warnings,
        name=name,
        description=description,
        body=body,
        raw_frontmatter=fm,
    )


def main() -> int:
    """CLI: validate a file, print JSON, exit 0 on valid / 1 on invalid."""
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Validate a SKILL.md file.")
    ap.add_argument("path", help="Path to SKILL.md")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    result = validate_skill_file(Path(args.path))

    if args.json:
        out = result.as_dict()
        # Don't dump the entire body in JSON — too noisy
        if out.get("body"):
            out["body_length"] = len(out["body"])
            del out["body"]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if result.valid:
            print(f"OK  {args.path}")
            print(f"    name: {result.name}")
            print(f"    description ({len(result.description or '')} chars)")
            print(f"    body ({len((result.body or '').encode('utf-8')) / 1024:.1f} KB)")
            for w in result.warnings:
                print(f"    WARN: {w}")
        else:
            print(f"FAIL  {args.path}")
            for e in result.errors:
                print(f"    ERR: {e}")
            for w in result.warnings:
                print(f"    WARN: {w}")

    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
