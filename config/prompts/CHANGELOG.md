# Prompt CHANGELOG

Prompt version is part of the cache key and of `RunReport.prompt_version`.
A version bump invalidates the cache — update the version string in this file
when changing the prompt text.

| Version | Date       | Change                        |
|---------|------------|-------------------------------|
| v1      | 2026-10-19 | Initial skeleton prompt (T3)  |

## Rules for versioning

- Bump the version (v1 → v2) for any wording change that could affect extraction quality.
- Document the reason and expected accuracy impact in this table.
- Reference the PR and the before/after eval numbers (§0.5 rule 7).
- Never change v1.txt after merging — create v2.txt instead.
