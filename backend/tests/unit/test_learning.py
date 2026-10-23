"""The learning layer: what is remembered, when it is used and what it changes (PLAN §7, T24)."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path

import pytest

from backend.adapters.imaging.cv import CvImaging
from backend.adapters.learning.sqlite_store import SqliteLearningStore
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.learning import (
    abstract_shape,
    observations_from_row,
    shape_to_regex,
    with_learned,
)
from backend.core.models import (
    Box,
    ConfidenceLevel,
    FieldKey,
    LayoutObservation,
    NormalizedPage,
    OcrResult,
    OcrWord,
    PageRaw,
    ReaderName,
    Region,
    RegionKind,
    SenderSpec,
    SourceKind,
)
from backend.core.pipeline.orchestrator import process_page
from backend.core.pipeline.s4_regions import LEARNED_DETECTOR, detect_regions
from backend.core.pipeline.s10_rows import assemble_rows
from backend.core.validators import pattern_fit
from backend.settings import Settings
from backend.tests.fixtures.layout import barcode, region
from backend.tests.fixtures.rows import ctx, page
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page
from backend.tests.fixtures.synthetic.pipeline_doubles import (
    PageScriptedBarcode,
    PageScriptedOcr,
    PageScriptedRegions,
)
from backend.tests.fixtures.vision_scenarios import Services, filler_words

SENDER = "בית חולים הדסה"
STICKER_BOX: Box = (0.045, 0.49, 0.11, 0.82)
OBSERVATION = LayoutObservation(
    sender=SENDER,
    field=FieldKey.central_number,
    region_kind=RegionKind.central_sticker,
    box=STICKER_BOX,
    source=SourceKind.barcode,
    page_role="primary_form",
)


@pytest.fixture
def store(tmp_path: Path) -> SqliteLearningStore:
    return SqliteLearningStore(settings=Settings(_env_file=None, data_dir=str(tmp_path)))  # type: ignore[call-arg]


def confirm(store: SqliteLearningStore, obs: LayoutObservation, times: int) -> None:
    for _ in range(times):
        store.record_confirmation(obs)


def hint_count(store: SqliteLearningStore) -> int:
    return len(store.layout_hints(SENDER, FieldKey.central_number.value))


# --- what a row teaches -----------------------------------------------------------------------


def test_shape_abstraction_discards_digits() -> None:
    assert abstract_shape("X132767") == "A######"
    assert abstract_shape("132767") == "######"
    assert abstract_shape("12-34") == "##-##"


def test_a_shape_is_a_regex_hint_not_a_value() -> None:
    assert pattern_fit("X132767", [shape_to_regex("A######")]) > pattern_fit(
        "1327", [shape_to_regex("A######")]
    )
    assert shape_to_regex("A######") == r"^[^\W\d_]\d\d\d\d\d\d$"


def test_row_teaches_locations_and_shapes_only(store: SqliteLearningStore) -> None:
    pages = [
        page(
            "p1", centrals=["937309"], date="18/06/2026", commitments=["X132767"], first_name="דנה"
        )
    ]
    rows, _, _ = assemble_rows(pages, ctx=ctx())

    observations = observations_from_row(rows[0], pages)

    assert {o.field for o in observations} >= {FieldKey.commitment_number, FieldKey.central_number}
    shaped = next(o for o in observations if o.field is FieldKey.commitment_number)
    assert shaped.shape == "A######"
    dumped = json.dumps([o.model_dump(mode="json") for o in observations], ensure_ascii=False)
    for secret in ("X132767", "132767", "937309", "18/06/2026", "דנה"):
        assert secret not in dumped, f"{secret!r} reached an observation"
    for obs in observations:
        store.record_confirmation(obs)
    assert "132767" not in json.dumps(store.dump_tables(), ensure_ascii=False)


def test_only_commitment_numbers_have_a_shape() -> None:
    pages = [page("p1", centrals=["937309"], date="18/06/2026", commitments=["4521"])]
    rows, _, _ = assemble_rows(pages, ctx=ctx())

    shapes = {o.field: o.shape for o in observations_from_row(rows[0], pages)}

    assert shapes[FieldKey.commitment_number] == "####"
    assert all(v is None for k, v in shapes.items() if k is not FieldKey.commitment_number)


def test_orange_fields_are_not_learned_from() -> None:
    pages = [page("p1", centrals=["937309"], commitments=["4521"])]  # no stamp date: level D
    rows, _, _ = assemble_rows(pages, ctx=ctx())
    assert rows[0].fields[FieldKey.receipt_date].level is ConfidenceLevel.D

    fields = {o.field for o in observations_from_row(rows[0], pages)}

    assert FieldKey.receipt_date not in fields
    assert FieldKey.commitment_number in fields


def test_an_unlisted_sender_teaches_nothing() -> None:
    pages = [page("p1", centrals=["937309"], commitments=["4521"], sender="לא ברשימה")]
    rows, _, _ = assemble_rows(pages, ctx=ctx())

    assert observations_from_row(rows[0], pages) == []


def test_a_typed_value_has_no_known_place_and_teaches_nothing() -> None:
    pages = [page("p1", centrals=["937309"], date="18/06/2026", commitments=["4521"])]
    rows, _, _ = assemble_rows(pages, ctx=ctx())
    typed = (
        rows[0]
        .fields[FieldKey.commitment_number]
        .model_copy(update={"value": "9999", "box": (0.9, 0.9, 0.95, 0.95)})
    )
    row = rows[0].model_copy(
        update={"fields": {**rows[0].fields, FieldKey.commitment_number: typed}}
    )

    assert FieldKey.commitment_number not in {o.field for o in observations_from_row(row, pages)}


# --- promotion --------------------------------------------------------------------------------


def test_hint_not_promoted_below_three_hits(store: SqliteLearningStore) -> None:
    confirm(store, OBSERVATION, 2)

    assert hint_count(store) == 0


def test_hint_promoted_at_three_hits(store: SqliteLearningStore) -> None:
    confirm(store, OBSERVATION, 3)

    (hint,) = store.layout_hints(SENDER, FieldKey.central_number.value)
    assert hint.region_kind is RegionKind.central_sticker
    assert hint.box == pytest.approx(STICKER_BOX)
    assert 0.0 < hint.confidence <= 1.0


def test_the_location_is_the_mean_of_the_confirmations(store: SqliteLearningStore) -> None:
    for shift in (-0.02, 0.0, 0.02):
        moved = (STICKER_BOX[0] + shift, STICKER_BOX[1], STICKER_BOX[2] + shift, STICKER_BOX[3])
        store.record_confirmation(OBSERVATION.model_copy(update={"box": moved}))

    (hint,) = store.layout_hints(SENDER, FieldKey.central_number.value)

    assert hint.box == pytest.approx(STICKER_BOX)


def test_a_shape_is_promoted_at_three_hits_and_becomes_a_pattern(
    store: SqliteLearningStore,
) -> None:
    shaped = OBSERVATION.model_copy(
        update={
            "field": FieldKey.commitment_number,
            "shape": "A######",
            "region_kind": RegionKind.commitment_label,
        }
    )
    confirm(store, shaped, 2)
    assert store.pattern_hints(SENDER, "commitment_number") == []

    confirm(store, shaped, 1)

    (hint,) = store.pattern_hints(SENDER, "commitment_number")
    assert hint.pattern == shape_to_regex("A######")


def test_hints_are_per_sender_and_field(store: SqliteLearningStore) -> None:
    confirm(store, OBSERVATION, 3)

    assert store.layout_hints("another sender", "central_number") == []
    assert store.layout_hints(SENDER, "first_name") == []


def test_label_synonym_requires_approval(store: SqliteLearningStore) -> None:
    labelled = OBSERVATION.model_copy(
        update={"field": FieldKey.commitment_number, "label_text": "אסמכתא"}
    )
    confirm(store, labelled, 5)
    assert store.label_synonyms(SENDER, "commitment_number") == [], "seen often, never approved"

    (entry,) = store.proposals()["labels"]
    assert entry["approved"] == 0 and entry["hits"] == 5
    assert store.approve("label", entry["id"]) is True

    assert store.label_synonyms(SENDER, "commitment_number") == ["אסמכתא"]


def test_an_approved_label_below_the_promotion_threshold_is_still_unused(
    store: SqliteLearningStore,
) -> None:
    labelled = OBSERVATION.model_copy(
        update={"field": FieldKey.commitment_number, "label_text": "אסמכתא"}
    )
    confirm(store, labelled, 1)
    store.approve("label", store.proposals()["labels"][0]["id"])

    assert store.label_synonyms(SENDER, "commitment_number") == []


def test_sender_proposal_requires_approval(store: SqliteLearningStore) -> None:
    for _ in range(3):
        store.propose_sender("מכון חדש", ["מכון", "חדש"])
    assert store.approved_senders() == [], "confirmed three times, never approved"

    (proposal,) = store.proposals()["senders"]
    assert store.approve("sender", proposal["id"]) is True

    assert store.approved_senders() == [("מכון חדש", ["מכון", "חדש"])]


def test_approving_nothing_returns_false(store: SqliteLearningStore) -> None:
    assert store.approve("label", 999) is False
    assert store.approve("sender", 999) is False


# --- feeding the hints back -------------------------------------------------------------------


def spec() -> SenderSpec:
    return SenderSpec(
        canonical=SENDER,
        family="hospital",
        aliases=[SENDER],
        keywords=["הדסה"],
        commitment_labels=["מספר התחייבות"],
    )


def test_learned_patterns_and_approved_labels_widen_the_spec(store: SqliteLearningStore) -> None:
    labelled = OBSERVATION.model_copy(
        update={
            "field": FieldKey.commitment_number,
            "label_text": "אסמכתא",
            "shape": "####",
            "region_kind": RegionKind.commitment_label,
        }
    )
    confirm(store, labelled, 3)
    store.approve("label", store.proposals()["labels"][0]["id"])

    widened = with_learned(spec(), store)

    assert widened is not None
    assert widened.commitment_labels == ["מספר התחייבות", "אסמכתא"]
    assert widened.pattern_hints == [shape_to_regex("####")]
    assert spec().commitment_labels == ["מספר התחייבות"], "the closed list itself is untouched"


def test_nothing_learned_leaves_the_spec_as_it_was(store: SqliteLearningStore) -> None:
    original = spec()

    assert with_learned(original, store) is original
    assert with_learned(None, store) is None


def test_learned_region_competes_not_overrides(
    store: SqliteLearningStore,
    make_normalized: Callable[[Pixels], NormalizedPage],
    settings: Settings,
) -> None:
    confirm(store, OBSERVATION, 3)
    found = Region(
        kind=RegionKind.central_sticker, box=(0.2, 0.2, 0.3, 0.3), detector="rule_based", score=0.9
    )
    detector = PageScriptedRegions(regions={"page#1": [found]})
    normalized = make_normalized(clean_page())

    regions = detect_regions(
        normalized,
        OcrResult(reader=ReaderName.tesseract, words=[], text=""),
        [],
        settings=settings,
        sender=spec(),
        learning=store,
        detector=detector,
    )

    learned = [r for r in regions if r.detector == LEARNED_DETECTOR]
    assert found in regions, "the detector's own region is kept"
    assert len(learned) == 1 and learned[0].box == pytest.approx(STICKER_BOX)
    assert learned[0].score <= settings.learned_region_score < found.score


def test_no_learned_region_below_the_threshold(
    store: SqliteLearningStore,
    make_normalized: Callable[[Pixels], NormalizedPage],
    settings: Settings,
) -> None:
    confirm(store, OBSERVATION, 2)

    regions = detect_regions(
        make_normalized(clean_page()),
        OcrResult(reader=ReaderName.tesseract, words=[], text=""),
        [],
        settings=settings,
        sender=spec(),
        learning=store,
        detector=PageScriptedRegions(),
    )

    assert [r for r in regions if r.detector == LEARNED_DETECTOR] == []


# --- the point of it all ----------------------------------------------------------------------


class StubSenders:
    """A one-institution closed list that recognises any letterhead text as that institution."""

    min_ratio = 88

    def __init__(self, entry: SenderSpec) -> None:
        self.specs = (entry,)

    def get(self, canonical: str) -> SenderSpec | None:
        return self.specs[0] if canonical == self.specs[0].canonical else None

    def match(self, text: str, *, min_ratio: int | None = None) -> object:
        entry = self.specs[0]

        class Hit:
            canonical = entry.canonical
            score = 99.0
            spec = entry

        return Hit()


def sticker_page_services(
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
    learning: SqliteLearningStore,
    page_id: str,
) -> Services:
    """A page whose sticker the detector does not find: the caption, the bars and the digits are
    all on it, but only a learned region says where the sticker is."""
    caption = OcrWord(text="מרכזי", box=(0.050, 0.60, 0.062, 0.66), confidence=0.9)
    digits = OcrWord(text="4417528", box=(0.088, 0.55, 0.100, 0.75), confidence=0.9)
    bars = barcode("4417528", (0.065, 0.50, 0.085, 0.80), nearby=["מרכזי"])
    return Services(
        settings=settings,
        ocr_printed=PageScriptedOcr(words={page_id: [*filler_words(), caption, digits]}),
        barcode=PageScriptedBarcode(hits={page_id: [bars]}),
        regions=PageScriptedRegions(
            regions={page_id: [region(RegionKind.patient_box, (0.2, 0.05, 0.6, 0.95))]}
        ),
        vision=None,
        storage=storage,
        documents=None,
        imaging=imaging,
        senders=StubSenders(spec()),  # type: ignore[arg-type]
        learning=learning,  # type: ignore[arg-type]
    )


def closed_locally(outcome: object) -> int:
    results = outcome.results.values()  # type: ignore[attr-defined]
    return sum(1 for r in results if r.level is not ConfidenceLevel.D and not r.escalated)


def test_local_close_rate_improves_on_second_run(
    store: SqliteLearningStore,
    make_page: Callable[[Pixels], PageRaw],
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    raw = make_page(clean_page(width=900, height=1280))
    services = sticker_page_services(settings, storage, imaging, store, raw.page_id)

    cold = process_page(raw, services=services)  # nothing learned yet
    confirm(store, OBSERVATION, 3)  # three confirmed forms from this institution
    warm = process_page(raw, services=services)

    central_cold = cold.results[FieldKey.central_number]
    central_warm = warm.results[FieldKey.central_number]
    assert central_cold.level is ConfidenceLevel.D, (
        "without the learned region the sticker is unknown"
    )
    assert central_warm.level is not ConfidenceLevel.D and central_warm.value == "4417528"
    assert closed_locally(warm) > closed_locally(cold), "warm closes strictly more fields locally"


def test_turning_the_learning_layer_off_removes_the_improvement(
    store: SqliteLearningStore,
    make_page: Callable[[Pixels], PageRaw],
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
) -> None:
    confirm(store, OBSERVATION, 3)
    raw = make_page(clean_page(width=900, height=1280))
    services = sticker_page_services(settings, storage, imaging, store, raw.page_id)
    services.pipeline = services.pipeline.model_copy(
        update={"layers": services.pipeline.layers.model_copy(update={"learning": False})}
    )

    outcome = process_page(raw, services=services)

    assert outcome.results[FieldKey.central_number].level is ConfidenceLevel.D


# --- survival ---------------------------------------------------------------------------------


def test_store_survives_job_purge(store: SqliteLearningStore, tmp_path: Path) -> None:
    confirm(store, OBSERVATION, 3)
    jobs = TmpdirStorage(settings=Settings(_env_file=None, data_dir=str(tmp_path)))  # type: ignore[call-arg]
    jobs.put_page_image("job-1", "p1", b"\x89PNG", "color")

    jobs.purge_job("job-1")

    assert hint_count(store) == 1
    reopened = SqliteLearningStore(settings=Settings(_env_file=None, data_dir=str(tmp_path)))  # type: ignore[call-arg]
    assert len(reopened.layout_hints(SENDER, "central_number")) == 1, "a restart keeps it too"


def test_seed_round_trips_and_only_fills_an_empty_store(
    store: SqliteLearningStore, tmp_path: Path
) -> None:
    confirm(store, OBSERVATION, 3)
    seed = store.seed()
    other = SqliteLearningStore(
        settings=Settings(_env_file=None, data_dir=str(tmp_path / "other")),  # type: ignore[call-arg]
    )

    assert other.import_seed(seed) == 1
    assert len(other.layout_hints(SENDER, "central_number")) == 1
    assert other.import_seed(seed) == 0, "a second import never double counts"


def test_a_seed_with_an_unexpected_key_is_refused(
    store: SqliteLearningStore, tmp_path: Path
) -> None:
    confirm(store, OBSERVATION, 3)
    seed = store.seed()
    seed["layout_hints"][0]["value"] = "937309"
    empty = SqliteLearningStore(
        settings=Settings(_env_file=None, data_dir=str(tmp_path / "x")),  # type: ignore[call-arg]
    )

    with pytest.raises(ValueError, match="unexpected keys"):
        empty.import_seed(seed)


def test_reader_outcomes_count_only_person_decided_name_disputes() -> None:
    """Which reader held the name a person chose - a role and a bool, never the name."""
    from backend.core.learning import reader_outcomes
    from backend.core.models import Candidate, ReaderName, ReadingTrailStep, RegionKind, SourceKind
    from backend.tests.unit.export_helpers import make_field, make_row

    def cand(value: str, *notes: str) -> Candidate:
        return Candidate(
            value=value,
            reader=ReaderName.gemini,
            source=SourceKind.handwritten,
            box=None,
            reader_confidence=0.5,
            label_distance=None,
            region=RegionKind.patient_box,
            notes=["cloud_read", *notes],
        )

    human = ReadingTrailStep(
        stage="review",
        reader=ReaderName.human,
        outcome="value",
        value=None,
        reader_confidence=None,
        duration_ms=0,
        cloud=False,
    )
    disputed = make_field(FieldKey.first_name, "פינחסוביץ").model_copy(
        update={
            "candidates": [
                cand("ויקטוריה"),
                cand("ישראל", "second_read"),
                cand("פינחסוביץ", "third_read"),
            ],
        }
    )
    row = make_row()
    decided = disputed.model_copy(update={"trail": [*disputed.trail, human]})
    with_person = row.model_copy(update={"fields": {**row.fields, FieldKey.first_name: decided}})
    without = row.model_copy(update={"fields": {**row.fields, FieldKey.first_name: disputed}})

    assert sorted(reader_outcomes(with_person)) == [
        (FieldKey.first_name, "page", False),
        (FieldKey.first_name, "verify", False),
        (FieldKey.first_name, "zoom", True),
    ]
    assert reader_outcomes(without) == []  # accepted as shown: teaches nothing


def test_reader_outcomes_are_counted_per_field_and_reader(store: SqliteLearningStore) -> None:
    for right in (True, True, False):
        store.record_reader_outcome("first_name", "zoom", right=right)
    store.record_reader_outcome("first_name", "page", right=False)

    stats = {(s.field, s.reader): (s.disputes, s.right) for s in store.reader_stats()}

    assert stats == {(FieldKey.first_name, "zoom"): (3, 2), (FieldKey.first_name, "page"): (1, 0)}


def test_reader_outcomes_are_counted_per_field_and_reader(store: SqliteLearningStore) -> None:
    for right in (True, True, False):
        store.record_reader_outcome("first_name", "zoom", right=right)
    store.record_reader_outcome("first_name", "page", right=False)

    stats = {(s.field, s.reader): (s.disputes, s.right) for s in store.reader_stats()}

    assert stats == {(FieldKey.first_name, "zoom"): (3, 2), (FieldKey.first_name, "page"): (1, 0)}
