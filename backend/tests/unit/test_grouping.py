"""S10 grouping: which pages belong to which form (PLAN §4 S10, T20)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from backend.core.models import PageGroup
from backend.core.pipeline.outcome import PageOutcome
from backend.core.pipeline.s10_rows import (
    SIGNAL_BLANK,
    SIGNAL_CENTRAL,
    SIGNAL_DATE,
    SIGNAL_SEQUENCE,
    group_pages,
    pages_accounted_for,
)
from backend.tests.fixtures.rows import ctx, page

DATE = "18/06/2026"
OTHER_DATE = "19/06/2026"


class Resolver:
    def __init__(self, verdict: bool | None) -> None:
        self.verdict = verdict
        self.asked = 0

    def same_form(self, group_pages: Sequence[PageOutcome], page: PageOutcome) -> bool | None:
        self.asked += 1
        return self.verdict


def ids(groups: list[PageGroup]) -> list[list[str]]:
    return [g.page_ids for g in groups]


def test_sap_page_attaches_to_preceding_form() -> None:
    pages = [
        page("p1", centrals=["937309"], date=DATE),
        page("p2", "sap_screen", centrals=["937309"]),
    ]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1", "p2"]]
    assert orphans == []


def test_never_groups_by_national_id() -> None:
    same_id = "011223344"
    pages = [
        page("p1", centrals=["111111"], date=DATE, national_id=same_id),
        page("p2", centrals=["222222"], date=OTHER_DATE, national_id=same_id),
        page("p3", "sap_screen", centrals=["333333"], date="01/01/2026", national_id=same_id),
    ]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1"], ["p2"]], "two forms of one patient stay two forms"
    assert orphans == ["p3"], "an identical ID is no reason to attach a page"


def test_continuation_by_page_sequence() -> None:
    pages = [
        page("p1", sequence=(1, 3)),
        page("p2", "continuation", sequence=(2, 3)),
        page("p3", "continuation", sequence=(3, 3)),
    ]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1", "p2", "p3"]]
    assert groups[0].grouped_by == [SIGNAL_SEQUENCE]
    assert orphans == []


def test_page_two_follows_a_form_that_prints_no_marker() -> None:
    pages = [page("p1"), page("p2", "continuation", sequence=(2, 2))]

    groups, _ = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1", "p2"]]


@pytest.mark.parametrize(
    "marker",
    [(2, 5), (3, 3), (1, 3)],
    ids=["total differs", "skips a page", "not a continuation"],
)
def test_wrong_sequence_does_not_attach(marker: tuple[int, int]) -> None:
    pages = [page("p1", sequence=(1, 3)), page("p2", "continuation", sequence=marker)]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert orphans == ["p2"]
    assert ids(groups) == [["p1"]]


def test_grouping_by_matching_central_number() -> None:
    pages = [
        page("p1", centrals=["937309"], date=DATE),
        page("p2", "lab_report", centrals=["937309"], date=OTHER_DATE),
    ]

    groups, _ = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1", "p2"]]
    assert groups[0].grouped_by == [SIGNAL_CENTRAL]


def test_grouping_by_stamp_date_alone() -> None:
    pages = [page("p1", centrals=["937309"], date=DATE), page("p2", "sap_screen", date=DATE)]

    groups, _ = group_pages(pages, ctx=ctx())

    assert groups[0].grouped_by == [SIGNAL_DATE]


def test_unattachable_page_becomes_orphan() -> None:
    pages = [
        page("p1", centrals=["937309"], date=DATE),
        page("p2", "sap_screen", centrals=["555555"], date=OTHER_DATE),
    ]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1"]]
    assert orphans == ["p2"]


def test_page_before_any_form_is_an_orphan() -> None:
    pages = [page("p1", "sap_screen", centrals=["1"]), page("p2")]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert orphans == ["p1"]
    assert ids(groups) == [["p2"]]


@pytest.mark.parametrize("role", [None, "unknown"])
def test_unclassified_page_is_an_orphan(role: str | None) -> None:
    pages = [page("p1"), page("p2", role)]  # type: ignore[arg-type]

    _, orphans = group_pages(pages, ctx=ctx())

    assert orphans == ["p2"]


def test_failed_page_is_surfaced_not_dropped() -> None:
    pages = [page("p1"), page("p2", status="failed")]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert orphans == ["p2"]
    assert pages_accounted_for(len(pages), groups, orphans)


def test_a_stray_page_does_not_close_the_open_group() -> None:
    pages = [
        page("p1", centrals=["937309"]),
        page("p2", "sap_screen", centrals=["555555"]),
        page("p3", "sap_screen", centrals=["937309"]),
    ]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1", "p3"]]
    assert orphans == ["p2"]


def test_stray_single_digit_never_ties_two_pages() -> None:
    pages = [
        page("p1", centrals=["937309"], stray=["7"]),
        page("p2", "sap_screen", centrals=["555555"], stray=["7"]),
    ]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1"]]
    assert orphans == ["p2"]


def test_blank_page_joins_the_open_group_quietly() -> None:
    pages = [page("p1"), page("p2", "blank")]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert ids(groups) == [["p1", "p2"]]
    assert groups[0].grouped_by == [SIGNAL_BLANK]
    assert orphans == []


def test_blank_page_before_any_form_is_an_orphan() -> None:
    _, orphans = group_pages([page("p1", "blank")], ctx=ctx())

    assert orphans == ["p1"]


def test_each_form_opens_its_own_group() -> None:
    pages = [page("p1"), page("p2"), page("p3")]

    groups, _ = group_pages(pages, ctx=ctx())

    assert [g.group_id for g in groups] == ["grp-p1", "grp-p2", "grp-p3"]


def test_group_takes_the_sender_of_its_pages() -> None:
    pages = [page("p1", sender=None), page("p2", "sap_screen", sender="הדסה", date=DATE)]
    pages[0] = page("p1", sender=None, date=DATE)

    groups, _ = group_pages(pages, ctx=ctx())

    assert groups[0].sender == "הדסה"


# --- ambiguity: same date, different centrals -------------------------------------------------


def ambiguous() -> list[PageOutcome]:
    return [
        page("p1", centrals=["111111"], date=DATE),
        page("p2", "sap_screen", centrals=["222222"], date=DATE),
    ]


def test_same_date_but_different_centrals_is_not_attached_without_a_resolver() -> None:
    groups, orphans = group_pages(ambiguous(), ctx=ctx())

    assert ids(groups) == [["p1"]]
    assert orphans == ["p2"]


@pytest.mark.parametrize(("verdict", "attached"), [(True, True), (False, False), (None, False)])
def test_resolver_decides_the_ambiguous_page(verdict: bool | None, attached: bool) -> None:
    resolver = Resolver(verdict)

    groups, orphans = group_pages(ambiguous(), ctx=ctx(resolver=resolver))

    assert resolver.asked == 1
    assert (len(groups[0].page_ids) == 2) is attached
    assert (orphans == []) is attached


def test_resolver_is_not_asked_when_a_signal_already_matches() -> None:
    resolver = Resolver(False)
    pages = [
        page("p1", centrals=["111111"], date=DATE),
        page("p2", "sap_screen", centrals=["111111"], date=DATE),
    ]

    groups, _ = group_pages(pages, ctx=ctx(resolver=resolver))

    assert resolver.asked == 0
    assert ids(groups) == [["p1", "p2"]]


# --- accounting -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "layout",
    [
        ["primary_form"],
        ["primary_form", "sap_screen", "sap_screen"],
        ["sap_screen", "primary_form", "blank", "unknown", "primary_form", "continuation"],
        ["blank", "blank"],
        [],
    ],
)
def test_every_page_is_in_exactly_one_group_or_the_orphans(layout: list[str]) -> None:
    pages = [page(f"p{i}", role) for i, role in enumerate(layout)]  # type: ignore[arg-type]

    groups, orphans = group_pages(pages, ctx=ctx())

    assert pages_accounted_for(len(pages), groups, orphans)
    seen = [pid for g in groups for pid in g.page_ids] + orphans
    assert sorted(seen) == sorted(p.page_id for p in pages)


def test_accounting_check_detects_a_lost_page() -> None:
    groups, orphans = group_pages([page("p1")], ctx=ctx())

    assert not pages_accounted_for(2, groups, orphans)
