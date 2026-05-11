"""Unit tests for skill_validator.

Run with:
    python -m unittest tests.test_validator

or (from repo root):
    python tests/test_validator.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make scripts/ importable when run as a script
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from skill_validator import (  # noqa: E402
    validate_skill_file,
    validate_skill_text,
    NAME_PATTERN,
)


FIXTURES = REPO_ROOT / "tests" / "fixtures"


class ValidatorTests(unittest.TestCase):
    def test_valid_basic(self):
        r = validate_skill_file(FIXTURES / "valid_basic.md")
        self.assertTrue(r.valid, msg=r.errors)
        self.assertEqual(r.name, "valid-basic")
        self.assertGreater(len(r.description or ""), 20)
        self.assertGreater(len(r.body or ""), 100)
        self.assertEqual(r.errors, [])

    def test_missing_description_rejected(self):
        r = validate_skill_file(FIXTURES / "missing_description.md")
        self.assertFalse(r.valid)
        self.assertTrue(
            any("description" in e for e in r.errors),
            msg=r.errors,
        )

    def test_bad_name_rejected(self):
        r = validate_skill_file(FIXTURES / "bad_name.md")
        self.assertFalse(r.valid)
        self.assertTrue(
            any("name=" in e or NAME_PATTERN.pattern in e for e in r.errors),
            msg=r.errors,
        )

    def test_short_description_rejected(self):
        r = validate_skill_file(FIXTURES / "short_description.md")
        self.assertFalse(r.valid)
        self.assertTrue(
            any("too short" in e and "description" in e for e in r.errors),
            msg=r.errors,
        )

    def test_short_body_rejected(self):
        r = validate_skill_file(FIXTURES / "short_body.md")
        self.assertFalse(r.valid)
        self.assertTrue(
            any("body" in e and "too short" in e for e in r.errors),
            msg=r.errors,
        )

    def test_prompt_injection_rejected(self):
        r = validate_skill_file(FIXTURES / "prompt_injection.md")
        self.assertFalse(r.valid)
        self.assertTrue(
            any("prompt-injection" in e for e in r.errors),
            msg=r.errors,
        )

    def test_no_frontmatter_rejected(self):
        r = validate_skill_file(FIXTURES / "no_frontmatter.md")
        self.assertFalse(r.valid)
        self.assertTrue(
            any("frontmatter" in e.lower() for e in r.errors),
            msg=r.errors,
        )

    def test_nonexistent_file(self):
        r = validate_skill_file(REPO_ROOT / "does-not-exist.md")
        self.assertFalse(r.valid)
        self.assertTrue(any("not found" in e for e in r.errors))

    def test_inline_text_valid(self):
        text = (
            "---\n"
            "name: inline-test\n"
            "description: An inline-supplied skill used to verify "
            "validate_skill_text() accepts strings as well as file paths.\n"
            "---\n\n"
            "# inline\n\n"
            + ("Body that is sufficiently long to clear the 100-character "
               "minimum so the only difference from a file-based test is "
               "the input channel.")
        )
        r = validate_skill_text(text)
        self.assertTrue(r.valid, msg=r.errors)
        self.assertEqual(r.name, "inline-test")

    def test_oversized_description(self):
        long_desc = "x" * 600
        text = (
            f"---\nname: oversize\ndescription: {long_desc}\n---\n\n"
            f"# title\n\n" + ("body " * 30)
        )
        r = validate_skill_text(text)
        self.assertFalse(r.valid)
        self.assertTrue(any("too long" in e for e in r.errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
