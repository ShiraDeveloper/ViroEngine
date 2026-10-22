"""S10 - group pages into forms and assemble the rows the laboratory needs (PLAN §4 S10, T20).

Pages are walked in upload order. A `primary_form` opens a group; a following `sap_screen`,
`lab_report` or `continuation` joins it when ANY of three signals holds: a central number in
common, the same stamp date, or an explicit "page N of M" sequence. The national ID is never a
signal: it is identical on every sample page and two samples of one patient on one day is normal.
A page that cannot be attached is an orphan, surfaced and never forced into a group. When the date
matches but the central numbers differ the page is ambiguous: an injected resolver may confirm it
(PLAN: one cheap model call per ambiguous group); without one it is left an orphan.

Rows: one per distinct commitment, one flagged row when there is none, every central number of the
group on every row, the other fields duplicated with their own confidence, trail and reason. No
value is ever invented: a field the pages disagree on is returned flagged `multiple_candidates`.
Pure functions over `PageOutcome`s; the clock and the sender list arrive through ports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

from pydantic import Field

from backend.core.geometry import iou
from backend.core.models import (
    REASON_TEXT,
    Candidate,
    CommitmentRow,
    ConfidenceLevel,
    FieldKey,
    FieldResult,
    PageGroup,
    SenderSpec,
    Strict,
)
from backend.core.normalize import normalize_central, normalize_commitment
from backend.core.pipeline.outcome import Assembly, PageOutcome
from backend.core.pipeline.s8_score import evaluate_field
from backend.core.ports.clock import ClockPort
from backend.core.ports.senders import SenderIndexPort
from backend.core.scoring import (
    CRITICAL_FIELDS,
    CROSS_FIELD_NOTE,
    DEFAULT_WEIGHTS,
    ScoringConfig,
    ScoringWeights,
)
from backend.core.validators import parse_il_date

# Structural: the names under which `PageGroup.grouped_by` records why a page joined a group.
SIGNAL_SEQUENCE: Final = "sequence"
SIGNAL_CENTRAL: Final = "central_number"
SIGNAL_DATE: Final = "stamp_date"
SIGNAL_BLANK: Final = "blank_page"

# Structural: roles that continue a form; `primary_form` opens one.
_ATTACHING_ROLES: Final = frozenset({"sap_screen", "lab_report", "continuation"})

_LEVEL_RANK: Final = {
    ConfidenceLevel.A: 0,
    ConfidenceLevel.B: 1,
    ConfidenceLevel.C: 2,
    ConfidenceLevel.D: 3,
}
_RANK_LEVEL: Final = {rank: level for level, rank in _LEVEL_RANK.items()}
# Structural: PLAN §5.2 order of the critical fields (iteration order must be deterministic).
_CRITICAL_ORDER: Final = (
    FieldKey.national_id,
    FieldKey.central_number,
    FieldKey.receipt_date,
    FieldKey.commitment_number,
)
# Structural: fields the row logic treats specially.
_SPECIAL: Final = frozenset({FieldKey.central_number, FieldKey.commitment_number})


class RowsConfig(Strict):
    """S10 thresholds. `config/models.yaml: rows` is authoritative; a test keeps these defaults
    equal to it."""

    # Two commitment readings whose boxes overlap at least this much (intersection over union)
    # are the same place on the page, hence one commitment read twice (S7's second read), not two.
    same_location_iou: float = Field(default=0.5, ge=0.0, le=1.0)


class SameFormResolver(Protocol):
    """Settles an ambiguous page (same stamp date, different central numbers)."""

    def same_form(self, group_pages: Sequence[PageOutcome], page: PageOutcome) -> bool | None:
        """True to attach, False to refuse, None when it cannot tell. May be a model call; it is
        asked once per ambiguous page and must not raise for a provider problem."""
        ...


@dataclass(frozen=True)
class RowContext:
    """What S10 needs besides the pages: a clock for re-scoring, the sender list for pattern
    hints, the scoring configuration and, optionally, an ambiguity resolver."""

    clock: ClockPort
    senders: SenderIndexPort | None = None
    weights: ScoringWeights = DEFAULT_WEIGHTS
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    config: RowsConfig = field(default_factory=RowsConfig)
    resolver: SameFormResolver | None = None


def _norm(key: FieldKey, raw: str) -> str | None:
    """The comparison key of a value: representation only, no digit or letter is invented."""
    if key is FieldKey.central_number:
        return normalize_central(raw)
    if key is FieldKey.commitment_number:
        return normalize_commitment(raw)
    if key is FieldKey.receipt_date:
        parsed = parse_il_date(raw)
        return parsed.isoformat() if parsed is not None else None
    return " ".join(raw.split()) or None


def _rank(result: FieldResult) -> tuple[int, float]:
    """Sort key, best first: level A..D, then higher score."""
    return (_LEVEL_RANK[result.level], -result.score)


class _Evidence:
    """Per-page values that can survive on their own, computed once per page and field.

    A value `survives` when it is level A-C when scored alone: this is what keeps a single-digit
    token beside a barcode from becoming a central number, and a decoy from joining two forms."""

    def __init__(self, ctx: RowContext) -> None:
        self._ctx = ctx
        self._cache: dict[tuple[str, FieldKey], dict[str, FieldResult]] = {}

    def survivors(self, page: PageOutcome, key: FieldKey) -> dict[str, FieldResult]:
        """{comparison key: result} of every value of `key` on `page` that survives alone, in the
        order the candidates were found. Empty when the page has none."""
        cached = self._cache.get((page.page_id, key))
        if cached is not None:
            return cached
        found: dict[str, FieldResult] = {}
        base = page.results.get(key)
        if base is not None:
            groups: dict[str, list[Candidate]] = {}
            for cand in base.candidates:
                norm = _norm(key, cand.value)
                if norm is not None:
                    groups.setdefault(norm, []).append(cand)
            decided = (
                _norm(key, base.value)
                if base.value and base.level is not ConfidenceLevel.D
                else None
            )
            for norm, cands in groups.items():
                if norm == decided:
                    found[norm] = base
                    continue
                if any(CROSS_FIELD_NOTE in c.notes for c in cands):
                    continue  # a number two fields claimed never stands on its own (ADR-017)
                alone = evaluate_field(
                    key,
                    cands,
                    spec=None,
                    clock=self._ctx.clock,
                    weights=self._ctx.weights,
                    config=self._ctx.scoring,
                    trail=base.trail,
                ).result
                if alone.level is not ConfidenceLevel.D and alone.value:
                    found[norm] = alone
            if decided is not None and decided not in found:
                found[decided] = base
        self._cache[(page.page_id, key)] = found
        return found


@dataclass
class _Open:
    """A group being built."""

    first: PageOutcome
    pages: list[PageOutcome]
    centrals: set[str]
    dates: set[str]
    last_sequence: tuple[int, int] | None
    signals: list[str] = field(default_factory=list)

    def join(self, page: PageOutcome, signals: Sequence[str], ev: _Evidence) -> None:
        self.pages.append(page)
        self.centrals |= set(ev.survivors(page, FieldKey.central_number))
        self.dates |= set(ev.survivors(page, FieldKey.receipt_date))
        if page.page_number is not None and page.page_total is not None:
            self.last_sequence = (page.page_number, page.page_total)
        for signal in signals:
            if signal not in self.signals:
                self.signals.append(signal)


def _sequence(page: PageOutcome) -> tuple[int, int] | None:
    if page.page_number is None or page.page_total is None:
        return None
    return page.page_number, page.page_total


def _open_group(page: PageOutcome, ev: _Evidence) -> _Open:
    group = _Open(first=page, pages=[], centrals=set(), dates=set(), last_sequence=None)
    group.join(page, [], ev)
    return group


def _attach_signals(group: _Open, page: PageOutcome, ev: _Evidence) -> tuple[list[str], bool]:
    """(signals that tie `page` to `group`, ambiguous). Ambiguous = the stamp dates match while
    both pages carry central numbers and none is shared, with no stronger signal."""
    signals: list[str] = []
    sequence = _sequence(page)
    if sequence is not None and sequence[0] > 1:
        previous = group.last_sequence
        follows = previous == (sequence[0] - 1, sequence[1])
        # A lone primary form that prints no marker of its own, followed by an explicit page 2.
        lone_first = previous is None and sequence[0] == 2 and len(group.pages) == 1
        if follows or lone_first:
            signals.append(SIGNAL_SEQUENCE)
    centrals = set(ev.survivors(page, FieldKey.central_number))
    if centrals & group.centrals:
        signals.append(SIGNAL_CENTRAL)
    date_match = bool(set(ev.survivors(page, FieldKey.receipt_date)) & group.dates)
    clash = bool(centrals) and bool(group.centrals) and not (centrals & group.centrals)
    if date_match and not clash:
        signals.append(SIGNAL_DATE)
    return signals, date_match and clash and not signals


def _group_id(first: PageOutcome) -> str:
    return f"grp-{first.page_id}"


def _has_values(page: PageOutcome) -> bool:
    """True when the page closed (level A-C) a central or commitment number: it is a sample."""
    return any(
        r.value and r.level is not ConfidenceLevel.D
        for k, r in page.results.items()
        if k in (FieldKey.central_number, FieldKey.commitment_number)
    )


def _walk(
    pages: Sequence[PageOutcome], ctx: RowContext, ev: _Evidence
) -> tuple[list[_Open], list[str]]:
    groups: list[_Open] = []
    orphans: list[str] = []
    current: _Open | None = None
    for page in pages:
        role = page.page_role
        if page.status != "done" or role is None:
            orphans.append(page.page_id)
        elif role == "primary_form" or (role == "unknown" and _has_values(page)):
            # A page whose role could not be told but that carries values is a form of its own:
            # dropping it to the orphans tab would lose a whole row from the spreadsheet.
            current = _open_group(page, ev)
            groups.append(current)
        elif role == "unknown":
            orphans.append(page.page_id)
        elif current is None:
            orphans.append(page.page_id)
        elif role == "blank":
            current.join(page, [SIGNAL_BLANK], ev)
        elif role in _ATTACHING_ROLES:
            signals, ambiguous = _attach_signals(current, page, ev)
            if ambiguous and ctx.resolver is not None:
                verdict = ctx.resolver.same_form(current.pages, page)
                if verdict is True:
                    signals = [SIGNAL_DATE]
            if signals:
                current.join(page, signals, ev)
            else:
                orphans.append(page.page_id)
        else:
            orphans.append(page.page_id)
    return groups, orphans


def _to_group(group: _Open) -> PageGroup:
    sender = next((p.sender for p in group.pages if p.sender is not None), None)
    return PageGroup(
        group_id=_group_id(group.first),
        page_ids=[p.page_id for p in group.pages],
        sender=sender,
        family=None,
        is_orphan=False,
        grouped_by=[s for s in group.signals if s != SIGNAL_BLANK]
        + ([SIGNAL_BLANK] if SIGNAL_BLANK in group.signals else []),
    )


def group_pages(
    pages: Sequence[PageOutcome], *, ctx: RowContext
) -> tuple[list[PageGroup], list[str]]:
    """(groups, orphan page ids) for pages in upload order. NEVER groups by national ID.

    Every page lands in exactly one group or in the orphan list, so
    `len(pages) == sum(len(g.page_ids) for g in groups) + len(orphans)`. A failed page is an
    orphan: it is surfaced, not dropped. Never raises."""
    groups, orphans = _walk(pages, ctx, _Evidence(ctx))
    return [_to_group(g) for g in groups], orphans


# ----------------------------------------------------------------------------------------- rows


def _best(results: Sequence[FieldResult]) -> FieldResult:
    return min(results, key=_rank)


def _disagreement(key: FieldKey, results: Sequence[FieldResult]) -> bool:
    """True when two pages closed the field (level A-C) on different values."""
    closed = {_norm(key, r.value) for r in results if r.level is not ConfidenceLevel.D and r.value}
    return len({v for v in closed if v is not None}) > 1


def _flag_disagreement(result: FieldResult) -> FieldResult:
    return result.model_copy(
        update={"level": ConfidenceLevel.D, "reason": REASON_TEXT["multiple_candidates"]}
    )


def _plain_field(key: FieldKey, pages: Sequence[PageOutcome]) -> FieldResult | None:
    """The page-level result for a field that is one value per form. Pages that closed it on
    different values are not resolved by picking one: the best is returned flagged."""
    results = [p.results[key] for p in pages if key in p.results]
    if not results:
        return None
    best = _best(results)
    return _flag_disagreement(best) if _disagreement(key, results) else best


def _central_field(
    pages: Sequence[PageOutcome], ev: _Evidence
) -> tuple[FieldResult | None, list[str]]:
    """(the central-number result for every row, the central numbers of the group). Every value
    that survives alone attaches; the group's level is the worst of them."""
    key = FieldKey.central_number
    survivors: dict[str, FieldResult] = {}
    for page in pages:
        for norm, result in ev.survivors(page, key).items():
            known = survivors.get(norm)
            if known is None or _rank(result) < _rank(known):
                survivors[norm] = result
    fallback = _plain_field(key, pages)
    if not survivors:
        return fallback, []
    values = [r.value or norm for norm, r in survivors.items()]
    if len(survivors) == 1:
        return next(iter(survivors.values())), values
    results = list(survivors.values())
    worst = max(results, key=_rank)
    joined = "; ".join(values)
    merged = FieldResult(
        field=key,
        value=joined,
        display_value=joined,
        level=worst.level,
        score=min(r.score for r in results),
        source=results[0].source,
        box=None,
        candidates=[c for r in results for c in r.candidates],
        validations=[],
        trail=list(results[0].trail),
        reason=None,
        escalated=any(r.escalated for r in results),
    )
    return merged, values


@dataclass
class _Cluster:
    """Candidates that are one commitment: read twice at one place, or the same value."""

    order: tuple[int, float, float]
    candidates: list[Candidate] = field(default_factory=list)
    trail_from: FieldResult | None = None


def _commitment_clusters(
    pages: Sequence[PageOutcome], config: RowsConfig, ev: _Evidence
) -> list[_Cluster]:
    """Clusters of the commitment candidates whose value survives alone (level A-C). A candidate
    S8 rejected - a nearby word, a date beside the label - never opens a row of its own."""
    key = FieldKey.commitment_number
    items: list[tuple[int, Candidate, FieldResult, str]] = []
    for index, page in enumerate(pages):
        result = page.results.get(key)
        if result is None:
            continue
        surviving = ev.survivors(page, key)
        for cand in result.candidates:
            norm = _norm(key, cand.value)
            if norm is not None and norm in surviving:
                items.append((index, cand, result, norm))
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (page_i, cand_i, _, norm_i) in enumerate(items):
        for j in range(i + 1, len(items)):
            page_j, cand_j, _, norm_j = items[j]
            same_value = norm_i == norm_j
            same_place = (
                page_i == page_j
                and cand_i.box is not None
                and cand_j.box is not None
                and iou(cand_i.box, cand_j.box) >= config.same_location_iou
            )
            if same_value or same_place:
                parent[find(j)] = find(i)
    clusters: dict[int, _Cluster] = {}
    for i, (page_index, cand, result, _) in enumerate(items):
        box = cand.box
        order = (page_index, box[0] if box else 2.0, box[1] if box else 2.0)
        cluster = clusters.setdefault(find(i), _Cluster(order=order, trail_from=result))
        cluster.order = min(cluster.order, order)
        cluster.candidates.append(cand)
    return sorted(clusters.values(), key=lambda c: c.order)


def _commitment_results(
    pages: Sequence[PageOutcome], spec: SenderSpec | None, ctx: RowContext, ev: _Evidence
) -> list[FieldResult]:
    """One result per distinct surviving commitment, in page then top-to-bottom order; a single
    result when the form has at most one, which keeps its page-level S8 result unchanged (flagged,
    with its own reason, when nothing survived)."""
    key = FieldKey.commitment_number
    clusters = _commitment_clusters(pages, ctx.config, ev)
    page_results = [p.results[key] for p in pages if key in p.results]
    if len(clusters) <= 1:
        if page_results:
            return [_best(page_results)]
        empty = evaluate_field(
            key, [], spec=spec, clock=ctx.clock, weights=ctx.weights, config=ctx.scoring
        )
        return [empty.result]
    return [
        evaluate_field(
            key,
            cluster.candidates,
            spec=spec,
            clock=ctx.clock,
            weights=ctx.weights,
            config=ctx.scoring,
            trail=cluster.trail_from.trail if cluster.trail_from is not None else (),
        ).result
        for cluster in clusters
    ]


def worst_critical_level(fields: dict[FieldKey, FieldResult]) -> ConfidenceLevel:
    """Worst level among the four critical fields; a missing field counts as D."""
    assert set(_CRITICAL_ORDER) == CRITICAL_FIELDS
    ranks = [
        _LEVEL_RANK[fields[k].level] if k in fields else _LEVEL_RANK[ConfidenceLevel.D]
        for k in _CRITICAL_ORDER
    ]
    return _RANK_LEVEL[max(ranks)]


def rows_from_group(
    g: PageGroup,
    pages: Sequence[PageOutcome],
    *,
    ctx: RowContext,
    evidence: _Evidence | None = None,
) -> list[CommitmentRow]:
    """The rows of one group. `pages` may hold more than the group's pages; only those named in
    `g.page_ids` are used.

    One row per distinct commitment (one flagged row when there is none); the central numbers of
    the whole group and every other field are repeated on each row as independent copies, each
    keeping its own confidence, trail and reason. Never raises."""
    ev = evidence or _Evidence(ctx)
    wanted = set(g.page_ids)
    members = [p for p in pages if p.page_id in wanted]
    spec = ctx.senders.get(g.sender) if ctx.senders is not None and g.sender else None
    central, central_numbers = _central_field(members, ev)
    shared: dict[FieldKey, FieldResult] = {}
    for key in FieldKey:
        if key in _SPECIAL:
            continue
        result = _plain_field(key, members)
        if result is not None:
            shared[key] = result
    if central is not None:
        shared[FieldKey.central_number] = central
    rows: list[CommitmentRow] = []
    for number, commitment in enumerate(_commitment_results(members, spec, ctx, ev), start=1):
        fields = {k: v.model_copy(deep=True) for k, v in shared.items()}
        fields[FieldKey.commitment_number] = commitment
        rows.append(
            CommitmentRow(
                row_id=f"row-{g.page_ids[0]}-{number}",
                group_id=g.group_id,
                page_ids=list(g.page_ids),
                fields=fields,
                central_numbers=list(central_numbers),
                row_level=worst_critical_level(fields),
                status="auto",
            )
        )
    return rows


def _suggest_merges(
    rows: list[CommitmentRow], hashes: dict[str, frozenset[str]]
) -> list[CommitmentRow]:
    """Mark a row that repeats an earlier one (the same page content and the same critical
    values) with `merge_suggestion`. Suggest only: nothing is merged or removed."""
    seen: dict[tuple[frozenset[str], tuple[str | None, ...]], str] = {}
    marked: list[CommitmentRow] = []
    for row in rows:
        content = frozenset().union(*(hashes.get(p, frozenset()) for p in row.page_ids))
        values = tuple(row.fields[k].value if k in row.fields else None for k in _CRITICAL_ORDER)
        signature = (content, values)
        earlier = seen.get(signature) if content else None
        if earlier is not None:
            marked.append(row.model_copy(update={"merge_suggestion": earlier}))
        else:
            if content:
                seen[signature] = row.row_id
            marked.append(row)
    return marked


def assemble_rows(pages: Sequence[PageOutcome], *, ctx: RowContext) -> Assembly:
    """S10 end to end: (rows, groups, orphan page ids) for the pages of one job, in upload order."""
    ev = _Evidence(ctx)
    open_groups, orphans = _walk(pages, ctx, ev)
    groups = [_to_group(g) for g in open_groups]
    rows: list[CommitmentRow] = []
    for group in groups:
        rows.extend(rows_from_group(group, pages, ctx=ctx, evidence=ev))
    hashes = {p.page_id: frozenset({p.sha256}) for p in pages if p.sha256}
    return _suggest_merges(rows, hashes), groups, orphans


def pages_accounted_for(pages: int, groups: Sequence[PageGroup], orphans: Sequence[str]) -> bool:
    """True when every page is in exactly one group or in the orphan list."""
    return pages == sum(len(g.page_ids) for g in groups) + len(orphans)
