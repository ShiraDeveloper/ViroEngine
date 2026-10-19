"""Tests for the sender closed list: config/senders.json, loader and tools/build_senders (T6).

test_all_observed_senders_present            - every §2.6 institution is in the committed list
test_alias_resolves                          - 'המרכז הרפואי שמיר' resolves to 'אסף הרופא'
test_destination_is_suppressed               - a letterhead of only the destination gives None
test_below_threshold_returns_none            - unrelated text and bare generic words give None
test_typo_tolerance                          - one OCR-swapped letter still resolves
test_laniado_pattern_allows_letter           - a commitment with a leading letter fits the hint
test_rosenblatt_has_no_commitment_labels     - no labels: the field stays empty and flagged
test_build_falls_back_to_seed_on_network_failure - HTTP 418 falls back; the build still succeeds
"""

# ruff: noqa: RUF001  (Hebrew letters are not confusable text here)

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re

import httpx
import pytest

from backend.adapters.senders.loader import DEFAULT_PATH, SenderIndex
from tools import build_senders
from tools.build_senders import build, fetch_hospitals_from_wikipedia, parse_hospital_names

SEED = DEFAULT_PATH.with_name("senders.seed.json")
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)

OBSERVED = [
    "מאוחדת", "כללית", "מכבי שירותי בריאות", "לאומית שירותי בריאות", "אסף הרופא", 'רמב"ם',
    "לניאדו", "וולפסון", "קפלן", "מאיר", "הלל יפה", "בני ציון", "שניידר", "אסותא אשדוד",
    "ברזילי", "כרמל", "שערי צדק", "מעבדת רוזנבלט", 'צה"ל - חיל הרפואה',
    "המכון לרפואה משפטית", "KGI",
]  # fmt: skip


@pytest.fixture(scope="module")
def index() -> SenderIndex:
    return SenderIndex.load()


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, timeout=5.0)


def test_all_observed_senders_present(index: SenderIndex) -> None:
    present = {s.canonical for s in index.specs}
    assert set(OBSERVED) <= present
    assert len(index.specs) >= 25
    assert index.source_url and index.fetched_at
    assert set(index.sources.values()) <= {"constant", "wikipedia", "moh", "seed"}


def test_every_entry_has_labels_deny_and_valid_hints(index: SenderIndex) -> None:
    for spec in index.specs:
        assert spec.aliases and spec.keywords
        assert set(build_senders.DEFAULT_COMMITMENT_DENY) <= set(spec.commitment_deny)
        for hint in spec.pattern_hints:
            re.compile(hint)
        if spec.canonical not in {"מעבדת רוזנבלט", "המכון לרפואה משפטית", "KGI"}:
            assert spec.commitment_labels, spec.canonical


@pytest.mark.parametrize(
    ("letterhead", "canonical"),
    [
        ("המרכז הרפואי שמיר", "אסף הרופא"),
        ("בית החולים אדית וולפסון", "וולפסון"),
        ('רמב"ם', 'רמב"ם'),
        ("רמבם", 'רמב"ם'),
        ("Laniado Hospital", "לניאדו"),
        ("לכבוד מעבדת נגיפים שיבא\nמכבי שירותי בריאות", "מכבי שירותי בריאות"),
    ],
)
def test_alias_resolves(index: SenderIndex, letterhead: str, canonical: str) -> None:
    hit = index.match(letterhead)
    assert hit is not None
    assert hit.canonical == canonical
    assert hit.score >= index.min_ratio


@pytest.mark.parametrize(
    "letterhead",
    ["אל: מעבדת נגיפים תל השומר", "לכבוד המרכז הרפואי שיבא", "המעבדה המרכזית לנגיפים"],
)
def test_destination_is_suppressed(index: SenderIndex, letterhead: str) -> None:
    assert index.match(letterhead) is None


@pytest.mark.parametrize(
    "text",
    ["מסמך כלשהו בלי שם", "בית חולים כללי", "מרכז רפואי", "הדסה", "", "   "],
)
def test_below_threshold_returns_none(index: SenderIndex, text: str) -> None:
    assert index.match(text) is None


def test_min_ratio_argument_overrides_config(index: SenderIndex) -> None:
    assert index.match("כלליח", min_ratio=101) is None


def test_typo_tolerance(index: SenderIndex) -> None:
    hit = index.match("כלליח")
    assert hit is not None
    assert hit.canonical == "כללית"


def test_laniado_pattern_allows_letter(index: SenderIndex) -> None:
    spec = index.get("לניאדו")
    assert spec is not None
    assert any(re.fullmatch(h, "X132767") for h in spec.pattern_hints)


def test_rosenblatt_has_no_commitment_labels(index: SenderIndex) -> None:
    spec = index.get("מעבדת רוזנבלט")
    assert spec is not None
    assert spec.commitment_labels == []
    assert "מס' קליטה" in spec.commitment_deny
    assert spec.notes and "NO commitment number" in spec.notes


def test_destination_names_are_not_senders(index: SenderIndex) -> None:
    names = {n for s in index.specs for n in (s.canonical, *s.aliases)}
    assert not names & {"שיבא", "תל השומר", "מעבדת נגיפים"}


def test_build_falls_back_to_seed_on_network_failure(tmp_path: Path) -> None:
    teapot = _client(httpx.MockTransport(lambda request: httpx.Response(418)))
    out = tmp_path / "senders.json"
    doc = build(
        SEED,
        out,
        fetch=lambda: fetch_hospitals_from_wikipedia(client=teapot),
        now=NOW,
    )
    assert doc["fetch_status"] == "seed_fallback"
    assert doc["fetched_at"] == NOW.isoformat(timespec="seconds")
    reloaded = SenderIndex.load(out)
    assert len(reloaded.specs) == len(json.loads(SEED.read_text("utf-8"))["senders"])


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(302, headers={"location": "https://block.example/418"}),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, json={"error": {"code": "missingtitle"}}),
        lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout("slow")),
    ],
    ids=["cross_origin_redirect", "not_json", "no_parse_key", "timeout"],
)
def test_fetch_returns_none_on_any_failure(handler: object) -> None:
    client = _client(httpx.MockTransport(handler))  # type: ignore[arg-type]
    assert fetch_hospitals_from_wikipedia(client=client) is None


WIKITEXT = """
{| class="wikitable"
|-
| [[המרכז הרפואי נוף הגליל|נוף הגליל]] || [[נצרת עילית]]
|-
| [[בית החולים הדסה עין כרם]] || [[ירושלים]]
|-
| [[בית החולים שמיר (אסף הרופא)|בית החולים שמיר]] || [[צריפין]]
|}
"""


def test_parse_hospital_names_keeps_only_hospital_links() -> None:
    names = parse_hospital_names(WIKITEXT)
    assert "בית החולים הדסה עין כרם" in names
    assert "נצרת עילית" not in names
    assert "ירושלים" not in names


def test_live_fetch_adds_unknown_hospitals_and_marks_known(tmp_path: Path) -> None:
    out = tmp_path / "senders.json"
    doc = build(
        SEED,
        out,
        fetch=lambda: ["בית החולים הדסה עין כרם", "בית החולים נוף הגליל"],
        now=NOW,
    )
    by_name = {s["canonical"]: s for s in doc["senders"]}
    assert doc["fetch_status"] == "live"
    assert by_name["הדסה עין כרם"]["source"] == "wikipedia"
    assert by_name["בית החולים נוף הגליל"]["source"] == "wikipedia"
    assert by_name["כללית"]["source"] == "constant"
    SenderIndex.load(out)


def test_rebuild_preserves_manual_enrichment(tmp_path: Path) -> None:
    out = tmp_path / "senders.json"
    doc = build(SEED, out, fetch=lambda: None, now=NOW)
    for entry in doc["senders"]:
        if entry["canonical"] == "כללית":
            entry["aliases"].append("קופת חולים כללית")
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rebuilt = build(SEED, out, fetch=lambda: None, now=NOW)
    clalit = next(s for s in rebuilt["senders"] if s["canonical"] == "כללית")
    assert "קופת חולים כללית" in clalit["aliases"]


def test_build_rejects_invalid_seed(tmp_path: Path) -> None:
    bad = tmp_path / "seed.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(build_senders.SendersBuildError):
        build(bad, tmp_path / "out.json", fetch=lambda: None)
