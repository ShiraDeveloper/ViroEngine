"""S11 - finalize a job: the run report, the job status and the purge schedule (PLAN §4 S11, T20).

The report holds counters only (project rule §7): no value, name, ID or date. The status is decided
from the ROWS the reviewer will see, not from the engine's per-page fields: a multi-central page is
level D at page level but its row is fine, so counting page fields would flag a job nobody needs to
review. Artefacts are purged `JOB_TTL_MINUTES` after creation; for a further while the job answers
`purged` so the UI can say so instead of showing a 404. Pure functions.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from backend.core.models import (
    CommitmentRow,
    ConfidenceLevel,
    CostCounters,
    JobStatus,
    PageGroup,
    RunReport,
)
from backend.core.pipeline.outcome import PageOutcome
from backend.core.pipeline.s10_rows import pages_accounted_for

# Structural: shown in run_report.json so a result can be traced to the code that made it.
ENGINE_VERSION: Final = "0.1.0"

# Structural: the error code recorded when S10 loses or duplicates a page. It is a bug, never a
# runtime condition, so it is reported in the job's errors instead of raising.
PAGE_ACCOUNTING_ERROR: Final = "PAGE_ACCOUNTING"


@dataclass(frozen=True)
class PurgeSchedule:
    """When a job's artefacts go and how long the job still answers `purged`."""

    expires_at: datetime  # artefacts purged at this instant
    purged_visible_until: datetime  # after this the job is simply unknown
    sweep_every: timedelta  # how often the sweeper looks for expired jobs


def purge_schedule(
    created_at: datetime, *, ttl_minutes: int, purged_visible_minutes: int, sweep_minutes: int
) -> PurgeSchedule:
    """The purge times of a job created at `created_at` (PLAN §4 S11). All durations in minutes.
    Raises ValueError for a non-positive TTL or sweep interval."""
    if ttl_minutes < 1 or sweep_minutes < 1 or purged_visible_minutes < 0:
        raise ValueError("ttl and sweep interval must be positive, visibility not negative")
    expires = created_at + timedelta(minutes=ttl_minutes)
    return PurgeSchedule(
        expires_at=expires,
        purged_visible_until=expires + timedelta(minutes=purged_visible_minutes),
        sweep_every=timedelta(minutes=sweep_minutes),
    )


def flagged_field_count(rows: Sequence[CommitmentRow]) -> int:
    """Level-D fields across the rows, each row counted separately (what the reviewer sees)."""
    return sum(1 for row in rows for f in row.fields.values() if f.level is ConfidenceLevel.D)


def build_report(
    job_id: str,
    outcomes: Sequence[PageOutcome],
    *,
    started_at: datetime,
    finished_at: datetime,
    row_count: int,
    page_count: int,
    intake_ms: int,
    prompt_version: str,
    cloud_consent: bool = False,
) -> RunReport:
    """Counters only: no value, name, ID or date ever enters the report (project rule §7).
    `fields_closed_local` counts fields that are not level D and did not leave the machine."""
    results = [r for o in outcomes for r in o.results.values()]
    levels = Counter(r.level.value for r in results)
    closed = sum(1 for r in results if r.level is not ConfidenceLevel.D and not r.escalated)
    durations: Counter[str] = Counter({"S0": intake_ms})
    for outcome in outcomes:
        durations.update(outcome.durations_ms)
    total = len(results)
    stats = [o.escalation for o in outcomes]
    failovers = list(dict.fromkeys(hop for s in stats for hop in s.failovers))
    return RunReport(
        job_id=job_id,
        started_at=started_at,
        finished_at=finished_at,
        page_count=page_count,
        row_count=row_count,
        fields_total=total,
        fields_closed_local=closed,
        fields_escalated=sum(1 for r in results if r.escalated),
        fields_flagged=levels.get(ConfidenceLevel.D.value, 0),
        tier1_calls=sum(s.calls for s in stats),
        tier2_calls=0,  # no full-page tier exists: only crops ever leave the machine
        local_close_rate=closed / total if total else 0.0,
        level_histogram={lv.value: levels.get(lv.value, 0) for lv in ConfidenceLevel},
        stage_durations_ms=dict(durations),
        corrections_histogram=dict(Counter(c for o in outcomes for c in o.corrections)),
        cost=CostCounters(
            provider_calls=sum(s.calls for s in stats),
            cache_hits=sum(s.cache_hits for s in stats),
            input_tokens=sum(s.input_tokens for s in stats),
            output_tokens=sum(s.output_tokens for s in stats),
            estimated_usd=round(sum(s.estimated_usd for s in stats), 6),
        ),
        provider_failovers=failovers,
        engine_version=ENGINE_VERSION,
        prompt_version=prompt_version,
        cloud_consent=cloud_consent,
    )


def final_status(
    outcomes: Sequence[PageOutcome],
    rows: Sequence[CommitmentRow],
    orphans: Sequence[str],
    errors: Sequence[str],
) -> JobStatus:
    """`failed` when no page was read; `needs_review` when any row has a level-D field, any page
    is an orphan or any error was recorded; otherwise `confirmed` (PLAN §4 S11)."""
    if not any(o.status == "done" for o in outcomes):
        return JobStatus.failed
    review = flagged_field_count(rows) > 0 or bool(orphans) or bool(errors)
    return JobStatus.needs_review if review else JobStatus.confirmed


def accounting_errors(
    page_count: int, groups: Sequence[PageGroup], orphans: Sequence[str]
) -> list[str]:
    """`[PAGE_ACCOUNTING]` when pages != grouped pages + orphans, else empty. `page_count` is
    the number of pages that reached S10."""
    return [] if pages_accounted_for(page_count, groups, orphans) else [PAGE_ACCOUNTING_ERROR]
