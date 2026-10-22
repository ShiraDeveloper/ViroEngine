# Prompt CHANGELOG

Prompt version is part of the cache key and of `RunReport.prompt_version`.
A version bump invalidates the cache — update the version string in this file
when changing the prompt text.

| Version | Date       | Change                                                                 |
|---------|------------|------------------------------------------------------------------------|
| v1      | 2026-10-19 | Initial skeleton prompt (T3). Never sent to a provider.                |
| v1      | 2026-10-24 | T18: `v1.txt` replaced by PLAN Appendix A (extraction) and `sender_v1.txt` added (letterhead question). Replaced in place, not bumped, because the T3 skeleton was never used: no provider adapter existed, so no cached result or measured number refers to it. |
| v1      | 2026-10-22 | T27: sender_v1.txt gained the rule that image text is data, never an instruction (it lacked it). Still in place, not bumped: neither prompt has been sent to a live provider. |

## Files

- `v1.txt` — S9 extraction of the unresolved fields of one page (crops, code candidates,
  optional redacted thumbnail). Sections: `SYSTEM`, `CONTEXT`, `USER`.
- `sender_v1.txt` — S5 fallback when text and logo matching failed: choose the issuer from the
  closed list, or `לא מוכר`.

## Rules for versioning

- Bump the version (v1 → v2) for any wording change that could affect extraction quality.
- Document the reason and expected accuracy impact in this table.
- Reference the PR and the before/after eval numbers (§0.5 rule 7).
- Never change v1.txt after merging — create v2.txt (and sender_v2.txt) instead.
- The model is deliberately not asked for a confidence: S8 computes it. Do not add one.
