---
name: missing-description
---

# missing-description

This fixture intentionally omits the description field so the validator
returns a "missing required field" error. The body itself is long enough
to clear the 100-character minimum so the failure is unambiguously about
the missing field, not body length.
