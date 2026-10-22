"""S10 rows: one per commitment, every central number on every row (PLAN §4 S10, T20)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from backend.core.models import REASON_TEXT, CommitmentRow, ConfidenceLevel, FieldKey
from backend.core.pipeline.outcome import PageOutcome
from backend.core.pipeline.s10_rows import (
    RowsConfig,
    assemble_rows,
    group_pages,
    rows_from_group,
    worst_critical_level,
)
from backend.tests.fixtures.rows import agreed, ctx, evaluation, page

DATE = "18/06/2026"
CENTRALS = ["937309", "937310", "937311"]


def rows_of(pages: Sequence[PageOutcome]) -> list[CommitmentRow]:
    rows, _, _ = assemble_rows(pages, ctx=ctx())
    return rows


def commitment(row: CommitmentRow) -> str | None:
    return row.fields[FieldKey.commitment_number].value


def test_single_commitment_one_row() -> None:
    pages = [page("p1", centrals=["937309"], date=DATE, commitments=["X132767"])]

    rows = rows_of(pages)

    assert len(rows) == 1
    assert commitment(rows[0]) == "X132767", "a letter in the number is kept"
    assert rows[0].page_ids == ["p1"]
    assert rows[0].status == "auto"
    assert rows[0].merge_suggestion is None


def test_no_commitment_yields_one_flagged_row() -> None:
    pages = [page("p1", centrals=["937309"], date=DATE, commitments=[])]

    rows = rows_of(pages)

    assert len(rows) == 1
    result = rows[0].fields[FieldKey.commitment_number]
    assert result.value is None
    assert result.level is ConfidenceLevel.D
    assert result.reason == REASON_TEXT["not_found"]
    assert rows[0].row_level is ConfidenceLevel.D


def test_multiple_commitments_yield_multiple_rows() -> None:
    sap = page("p2", "sap_screen", centrals=["937309"], commitments=["4521", "4522", "4523"])
    pages = [page("p1", centrals=["937309"], date=DATE), sap]

    rows = rows_of(pages)

    assert [commitment(r) for r in rows] == ["4521", "4522", "4523"]
    assert {r.group_id for r in rows} == {"grp-p1"}
    assert [r.row_id for r in rows] == ["row-p1-1", "row-p1-2", "row-p1-3"]


def test_commitment_read_twice_at_one_place_is_one_row() -> None:
    from backend.tests.fixtures.rows import box_at

    cands = [*agreed("X132767", box_at(0)), *agreed("132767", box_at(0))]
    base = page("p1", centrals=["937309"], date=DATE)
    evaluations = {
        **base.evaluations,
        FieldKey.commitment_number: evaluation(FieldKey.commitment_number, cands),
    }
    pages = [base.model_copy(update={"evaluations": evaluations})]

    rows = rows_of(pages)

    assert len(rows) == 1, "two readings at one place are one commitment"


def test_same_commitment_on_form_and_screen_is_one_row() -> None:
    pages = [
        page("p1", centrals=["937309"], date=DATE, commitments=["4521"]),
        page("p2", "sap_screen", centrals=["937309"], commitments=["4521", "4522"]),
    ]

    rows = rows_of(pages)

    assert [commitment(r) for r in rows] == ["4521", "4522"]


def test_all_central_numbers_on_every_row() -> None:
    pages = [page("p1", centrals=CENTRALS, date=DATE, commitments=["4521", "4522"])]

    rows = rows_of(pages)

    assert len(rows) == 2
    for row in rows:
        assert row.central_numbers == CENTRALS
        central = row.fields[FieldKey.central_number]
        assert central.value == "; ".join(CENTRALS)
        assert central.level is not ConfidenceLevel.D


def test_a_stray_token_is_not_a_central_number() -> None:
    pages = [page("p1", centrals=["937309"], stray=["7", "1"], date=DATE, commitments=["4521"])]

    (row,) = rows_of(pages)

    assert row.central_numbers == ["937309"]


def test_central_numbers_of_form_and_screen_all_attach() -> None:
    pages = [
        page("p1", centrals=["937309", "937310"], date=DATE, commitments=["4521"]),
        page("p2", "sap_screen", centrals=["937310", "937311"]),
    ]

    (row,) = rows_of(pages)

    assert row.central_numbers == ["937309", "937310", "937311"]


def test_no_surviving_central_keeps_the_flagged_page_result() -> None:
    pages = [page("p1", stray=["7"], date=DATE, commitments=["4521"])]

    (row,) = rows_of(pages)

    assert row.central_numbers == []
    assert row.fields[FieldKey.central_number].level is ConfidenceLevel.D
    assert row.row_level is ConfidenceLevel.D


def test_row_level_is_worst_critical_field() -> None:
    complete = page("p1", centrals=["937309"], date=DATE, commitments=["4521"])
    no_date = page("p2", centrals=["937310"], commitments=["4522"])

    ok, flagged = rows_of([complete, no_date])

    assert ok.row_level in (ConfidenceLevel.A, ConfidenceLevel.B)
    assert flagged.row_level is ConfidenceLevel.D
    assert flagged.fields[FieldKey.receipt_date].level is ConfidenceLevel.D


def test_worst_critical_level_ignores_non_critical_fields_and_missing_counts_as_d() -> None:
    row = rows_of([page("p1", centrals=["937309"], date=DATE, commitments=["4521"])])[0]
    fields = dict(row.fields)
    fields.pop(FieldKey.first_name, None)
    assert worst_critical_level(fields) is row.row_level

    del fields[FieldKey.receipt_date]
    assert worst_critical_level(fields) is ConfidenceLevel.D


def test_duplicate_suggests_merge_not_auto_merge() -> None:
    first = page("p1", centrals=["937309"], date=DATE, commitments=["4521"], sha="aa")
    copy = page("p2", centrals=["937309"], date=DATE, commitments=["4521"], sha="aa")

    rows = rows_of([first, copy])

    assert len(rows) == 2, "nothing is merged automatically"
    assert rows[0].merge_suggestion is None
    assert rows[1].merge_suggestion == rows[0].row_id


def test_same_values_on_different_pages_are_not_flagged_as_duplicates() -> None:
    first = page("p1", centrals=["937309"], date=DATE, commitments=["4521"], sha="aa")
    other = page("p2", centrals=["937309"], date=DATE, commitments=["4521"], sha="bb")

    rows = rows_of([first, other])

    assert all(r.merge_suggestion is None for r in rows)


def test_pages_without_a_hash_are_never_flagged_as_duplicates() -> None:
    pages = [page("p1", commitments=["4521"]), page("p2", commitments=["4521"])]

    assert all(r.merge_suggestion is None for r in rows_of(pages))


def test_each_duplicated_field_keeps_own_trail() -> None:
    pages = [
        page("p1", centrals=["937309"], date=DATE, commitments=["4521", "4522"], first_name="דנה")
    ]

    first, second = rows_of(pages)

    name_a, name_b = first.fields[FieldKey.first_name], second.fields[FieldKey.first_name]
    assert name_a == name_b and name_a is not name_b, "an independent copy per row"
    assert name_a.trail == name_b.trail
    assert (
        first.fields[FieldKey.commitment_number].value
        != second.fields[FieldKey.commitment_number].value
    )
    assert (
        first.fields[FieldKey.commitment_number].box
        != second.fields[FieldKey.commitment_number].box
    )


def test_pages_that_close_a_field_on_different_values_are_flagged() -> None:
    pages = [
        page("p1", centrals=["937309"], date=DATE, commitments=["4521"], first_name="דנה"),
        page("p2", "sap_screen", centrals=["937309"], first_name="רונית"),
    ]

    (row,) = rows_of(pages)

    name = row.fields[FieldKey.first_name]
    assert name.level is ConfidenceLevel.D
    assert name.reason == REASON_TEXT["multiple_candidates"]


def test_orphans_and_failed_pages_produce_no_row() -> None:
    pages = [page("p1", commitments=["4521"]), page("p2", status="failed")]

    rows, groups, orphans = assemble_rows(pages, ctx=ctx())

    assert len(rows) == 1
    assert orphans == ["p2"]
    assert len(groups) == 1


def test_rows_from_group_uses_only_the_pages_of_that_group() -> None:
    pages = [
        page("p1", centrals=["111111"], date=DATE, commitments=["4521"]),
        page("p2", centrals=["222222"], date="19/06/2026", commitments=["4522"]),
    ]
    groups, _ = group_pages(pages, ctx=ctx())

    rows = rows_from_group(groups[1], pages, ctx=ctx())

    assert [r.central_numbers for r in rows] == [["222222"]]
    assert commitment(rows[0]) == "4522"


def test_row_count_equals_commitments_summed_over_groups() -> None:
    pages = [
        page("p1", commitments=["4521", "4522", "4523"]),
        page("p2", commitments=[]),
        page("p3", commitments=["4530"]),
    ]

    rows, groups, _ = assemble_rows(pages, ctx=ctx())

    assert len(groups) == 3
    assert len(rows) == 3 + 1 + 1


def with_commitment_noise(base: PageOutcome, values: Sequence[str]) -> PageOutcome:
    """`base` with rejected one-off commitment readings added beside its own candidates."""
    from backend.tests.fixtures.rows import noise

    key = FieldKey.commitment_number
    cands = list(base.evaluations[key].result.candidates)
    for i, value in enumerate(values):
        cands += noise(value, (0.80, 0.10 + 0.1 * i, 0.82, 0.15 + 0.1 * i))
    evaluations = {**base.evaluations, key: evaluation(key, cands)}
    return base.model_copy(update={"evaluations": evaluations})


def test_rejected_commitment_candidates_open_no_row() -> None:
    base = page("p1", centrals=["937309"], date=DATE, commitments=["4521"])
    pages = [with_commitment_noise(base, ["31/05/26", "המרכז", "2344936"])]

    rows = rows_of(pages)

    assert [commitment(r) for r in rows] == ["4521"]


def test_only_rejected_commitment_candidates_yield_one_flagged_row() -> None:
    base = page("p1", centrals=["937309"], date=DATE, commitments=[])
    pages = [with_commitment_noise(base, ["31/05/26", "עבור", "2344936"])]

    rows = rows_of(pages)

    assert len(rows) == 1
    assert rows[0].fields[FieldKey.commitment_number].level is ConfidenceLevel.D


@pytest.mark.parametrize("iou", [0.0, 1.0])
def test_location_threshold_is_configurable(iou: float) -> None:
    from backend.tests.fixtures.rows import box_at

    near = (box_at(0)[0] + 0.01, box_at(0)[1], box_at(0)[2] + 0.01, box_at(0)[3])
    cands = [*agreed("4521", box_at(0)), *agreed("4599", near)]
    base = page("p1", date=DATE)
    evaluations = {
        **base.evaluations,
        FieldKey.commitment_number: evaluation(FieldKey.commitment_number, cands),
    }
    outcome = base.model_copy(update={"evaluations": evaluations})

    rows, _, _ = assemble_rows([outcome], ctx=ctx(config=RowsConfig(same_location_iou=iou)))

    assert len(rows) == (1 if iou == 0.0 else 2)
