"""Vision-first engine (ADR-017): a page reading plus a blind crop re-read, decided by S8.

The provider is scripted, so these tests pin the decision logic: two agreeing readers are level
B, a decoded barcode that matches the reading is level A, disagreement is level D (a disputed name
stays visible, a disputed number does not), and a provider failure hands the page back to the
cascade instead of inventing anything.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from backend.adapters.clock.system import SystemClock
from backend.adapters.imaging.cv import CvImaging
from backend.adapters.senders.loader import SenderIndex
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.errors import ProviderExhausted
from backend.core.models import (
    REASON_TEXT,
    BarcodeHit,
    ConfidenceLevel,
    FieldKey,
    FieldStat,
    ImageRef,
    PageRaw,
    ProviderHealth,
    ReaderName,
    VisionRequest,
    VisionResponse,
)
from backend.core.pipeline.s10_rows import RowContext, assemble_rows
from backend.core.pipeline.vision_first import VisionServices, read_batch_vision
from backend.core.scoring import DEFAULT_WEIGHTS, ScoringConfig
from backend.core.vision_schema import PagePayload, label_belongs, name_plausible, page_answers
from backend.settings import Settings
from backend.tests.fixtures.synthetic.pages import Pixels, clean_page

ID = "011223344"  # passes the Israeli check digit
BOX = [100, 100, 140, 300]


def page_json(**over: object) -> str:
    """A schema-valid one-page answer; keyword arguments replace fields."""
    item = {"value": "", "source": "unknown", "box": [0, 0, 0, 0]}
    page: dict[str, object] = {
        "page": 1,
        "page_role": "primary_form",
        "sender": "מאוחדת",
        "sender_box": [20, 600, 60, 950],
        "first_name": {"value": "ישראל", "source": "handwritten", "box": BOX},
        "last_name": {"value": "ישראלי", "source": "handwritten", "box": [100, 320, 140, 500]},
        "national_id": {"value": ID, "source": "handwritten", "box": [160, 100, 190, 300]},
        "receipt_date": {"value": "18-06-2026", "source": "stamp", "box": [700, 100, 740, 300]},
        "central_number": [{"value": "939376", "source": "printed", "box": [30, 30, 60, 200]}],
        "commitment_number": [{"value": "730578626", "source": "printed", "box": BOX}],
        "ambiguities": [],
    }
    page.update(over)
    for key in ("first_name", "last_name", "national_id", "receipt_date"):
        page.setdefault(key, item)
    return json.dumps({"pages": [page]}, ensure_ascii=False)


@dataclass
class Scripted:
    """A VisionLLMPort that answers page requests with `page` and verify requests by crop
    field from `verify`. `fail` makes every request raise ProviderExhausted."""

    page: str = field(default_factory=page_json)
    verify: dict[FieldKey, list[str]] = field(default_factory=dict)
    fail: bool = False
    requests: list[VisionRequest] = field(default_factory=list)
    name: str = "scripted"

    def extract(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        if self.fail:
            raise ProviderExhausted("scripted failure")
        pages = []
        crops: list[list[str]] = []
        if request.task == "page":
            pages = page_answers(PagePayload.model_validate_json(self.page), 1)
        else:
            crops = [self.verify.get(c.field, []) for c in request.crops]
        return VisionResponse(
            raw_text="",
            candidates=[],
            provider=ReaderName.gemini,
            model_id="scripted",
            prompt_version="v1",
            duration_ms=1,
            pages=pages,
            crop_values=crops,
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, credentials=1, state="ok")


@dataclass
class Barcodes:
    payloads: list[str] = field(default_factory=list)
    name: str = "scripted_barcode"

    def sweep(
        self, image: ImageRef, orientations: tuple[int, ...] = (0, 90, 180, 270)
    ) -> list[BarcodeHit]:
        return [
            BarcodeHit(
                payload=p,
                symbology="code128",
                box=(0.03, 0.03, 0.06, 0.2),
                orientation_deg=0,
                nearby_text=[],
            )
            for p in self.payloads
        ]


@pytest.fixture
def raw(make_page: Callable[[Pixels], PageRaw]) -> PageRaw:
    return make_page(clean_page(width=900, height=1280))


def services(
    settings: Settings,
    storage: TmpdirStorage,
    imaging: CvImaging,
    vision: Scripted,
    verify: Scripted | None = None,
    barcodes: list[str] | None = None,
) -> VisionServices:
    return VisionServices(
        # The crop verifier is what these tests script; the whole-page verifier has its own test.
        settings=settings.model_copy(update={"verify_mode": "crops"}),
        vision=vision,
        verify=verify or vision,
        barcode=Barcodes(barcodes or []),
        imaging=imaging,
        storage=storage,
        senders=SenderIndex.load(),
        clock=SystemClock(),
        weights=DEFAULT_WEIGHTS,
        scoring=ScoringConfig(),
        prompt_version="v1",
    )


def test_two_agreeing_readers_close_the_field_at_b(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    verify = Scripted(
        verify={
            FieldKey.first_name: ["ישראל"],
            FieldKey.last_name: ["ישראלי"],
            FieldKey.national_id: ["11223344"],
            FieldKey.receipt_date: ["1 8 -06- 2026"],
        }
    )
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, Scripted(), verify)
    )

    assert outcome is not None
    results = outcome.results
    assert results[FieldKey.first_name].level is ConfidenceLevel.B
    assert results[FieldKey.first_name].value == "ישראל"
    assert results[FieldKey.last_name].level is ConfidenceLevel.B
    # The ID and the date are vouched for by code (check digit, calendar): not re-read, level C.
    assert results[FieldKey.national_id].value == ID  # lost leading zero padded back
    assert results[FieldKey.national_id].level is ConfidenceLevel.C
    assert results[FieldKey.receipt_date].level is ConfidenceLevel.C
    assert outcome.page_role == "primary_form"


def test_the_verifier_sees_crops_and_no_candidate(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    verify = Scripted()
    read_batch_vision([raw], services=services(settings, storage, imaging, Scripted(), verify))

    [request] = verify.requests
    assert request.task == "verify"
    assert request.crops and request.page_images == [] and request.page_thumb is None
    assert request.candidates == {}  # blind: the first answer is never shown


def test_a_matching_barcode_makes_level_a_without_a_crop(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    verify = Scripted()
    svc = services(settings, storage, imaging, Scripted(), verify, barcodes=["939376"])
    [outcome] = read_batch_vision([raw], services=svc)

    assert outcome is not None
    assert outcome.results[FieldKey.central_number].level is ConfidenceLevel.A
    sent = {c.field for r in verify.requests for c in r.crops}
    assert FieldKey.central_number not in sent  # confirmed by the barcode, not re-read


def test_disputed_name_stays_visible_but_flagged(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    verify = Scripted(verify={FieldKey.first_name: ["חיים"], FieldKey.national_id: ["011223345"]})
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, Scripted(), verify)
    )

    assert outcome is not None
    name = outcome.results[FieldKey.first_name]
    assert name.level is ConfidenceLevel.D
    assert name.reason == REASON_TEXT["readers_disagree"]
    assert name.value == "ישראל"  # shown for the technician to confirm
    # The second reading of the ID fails the check digit, so the check digit decides: the valid
    # reading stands, read once (amber), never green.
    national_id = outcome.results[FieldKey.national_id]
    assert national_id.level is ConfidenceLevel.C and national_id.value == ID


def test_a_lone_reading_is_amber_never_green(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, Scripted(), Scripted())
    )

    assert outcome is not None
    name = outcome.results[FieldKey.last_name]
    assert name.level is ConfidenceLevel.C and name.value == "ישראלי"
    assert outcome.results[FieldKey.national_id].level is ConfidenceLevel.C


def test_names_one_letter_apart_are_one_name(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    verify = Scripted(verify={FieldKey.first_name: ["ישרל"]})
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, Scripted(), verify)
    )

    assert outcome is not None
    name = outcome.results[FieldKey.first_name]
    assert name.level is ConfidenceLevel.C and name.value == "ישראל"


def test_a_lone_id_failing_the_check_digit_stays_orange(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    item = {"value": "123456789", "source": "handwritten", "box": [160, 100, 190, 300]}
    vision = Scripted(page=page_json(national_id=item))
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, vision, Scripted())
    )

    assert outcome is not None
    assert outcome.results[FieldKey.national_id].level is ConfidenceLevel.D


def test_a_sender_outside_the_closed_list_is_not_invented(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    vision = Scripted(page=page_json(sender="לא ברשימה"))
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, vision, Scripted())
    )

    assert outcome is not None
    sender = outcome.results[FieldKey.sender]
    assert sender.value is None and sender.level is ConfidenceLevel.D


def test_provider_failure_hands_the_page_back(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    outcomes = read_batch_vision(
        [raw], services=services(settings, storage, imaging, Scripted(fail=True))
    )

    assert outcomes == [None]


def test_a_failed_verifier_leaves_single_readings_flagged(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, Scripted(), Scripted(fail=True))
    )

    assert outcome is not None
    assert outcome.results[FieldKey.first_name].level is ConfidenceLevel.C
    assert outcome.results[FieldKey.first_name].value == "ישראל"


def test_a_number_claimed_by_two_fields_is_flagged_in_both(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    # The model took the commitment barcode for the central sticker: the barcode confirms the
    # digits, so without the cross-field rule this would be a green, wrong central number.
    item = {"value": "260630224", "source": "printed", "box": [30, 30, 60, 200]}
    vision = Scripted(page=page_json(central_number=[item], commitment_number=[item]))
    svc = services(settings, storage, imaging, vision, Scripted(), barcodes=["260630224"])
    [outcome] = read_batch_vision([raw], services=svc)

    assert outcome is not None
    for key in (FieldKey.central_number, FieldKey.commitment_number):
        result = outcome.results[key]
        assert result.level is ConfidenceLevel.D
        assert result.reason == REASON_TEXT["multiple_candidates"]

    # Row assembly re-scores values on their own; the conflict must survive it.
    rows, _, _ = assemble_rows([outcome], ctx=RowContext(clock=SystemClock()))
    assert rows and all(
        row.fields[FieldKey.central_number].level is ConfidenceLevel.D for row in rows
    )


def test_values_code_can_vouch_for_are_not_re_read(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    verify = Scripted()
    read_batch_vision([raw], services=services(settings, storage, imaging, Scripted(), verify))

    sent = {c.field for r in verify.requests for c in r.crops}
    assert FieldKey.national_id not in sent  # the check digit vouches for it
    assert FieldKey.receipt_date not in sent  # a real, past date
    assert {FieldKey.first_name, FieldKey.last_name} <= sent  # names always get a second read


def test_whole_page_second_read_by_another_model_closes_at_b(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    svc = services(settings, storage, imaging, Scripted(), Scripted())
    svc = VisionServices(**{**svc.__dict__, "settings": settings})  # default: verify_mode="page"
    [outcome] = read_batch_vision([raw], services=svc)

    assert outcome is not None
    assert outcome.results[FieldKey.first_name].level is ConfidenceLevel.B
    assert outcome.results[FieldKey.commitment_number].level is ConfidenceLevel.B


def test_a_name_read_from_the_address_line_is_dropped(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    first = {"value": "אלעזר", "source": "printed", "box": BOX}
    last = {"value": "שלמוביץ", "source": "printed", "box": BOX}
    page = json.loads(page_json(first_name=first, last_name=last))
    page["pages"][0]["patient_address"] = "שלמציון המלכה 241 אלעזר"
    vision = Scripted(page=json.dumps(page, ensure_ascii=False))
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, vision, Scripted())
    )

    assert outcome is not None
    assert outcome.results[FieldKey.first_name].value is None
    assert outcome.results[FieldKey.last_name].value is None


@pytest.mark.parametrize(
    ("key", "label", "belongs"),
    [
        (FieldKey.last_name, "שם משפחה", True),
        (FieldKey.first_name, "כתובת", False),
        (FieldKey.first_name, "שם האב", False),
        (FieldKey.first_name, "שם פרטי", True),
        (FieldKey.first_name, "מטופל:", True),
        (FieldKey.first_name, "First Name", True),
        (FieldKey.last_name, "שם ומשפחה", True),
        (FieldKey.first_name, "", True),  # the patient sticker has no label
        (FieldKey.first_name, "ת.ז", False),  # a name label is required
        (FieldKey.last_name, "מחלקה", False),
        (FieldKey.first_name, "בדיקה", False),
        (FieldKey.national_id, "ת.ז", True),
        (FieldKey.national_id, "מקרה", False),
        (FieldKey.commitment_number, "מס' קליטה", False),
        (FieldKey.commitment_number, "דרישה:", True),
        (FieldKey.central_number, "", True),
    ],
)
def test_a_value_is_kept_only_beside_its_own_label(
    key: FieldKey, label: str, belongs: bool
) -> None:
    assert label_belongs(key, label) is belongs


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("ישראל", True),
        ("בן דוד", True),
        ("CMV IgG", False),
        ("בדיקת דם", False),
        ("ישראל 2", False),
        ("מכבי", False),
        ("כללית", False),
        ("מאוחדת", False),
    ],
)
def test_a_name_with_digits_or_clinical_words_is_rejected(value: str, ok: bool) -> None:
    assert name_plausible(value) is ok


def test_the_same_word_as_first_and_family_name_is_flagged(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    same = {"value": "יוסף", "source": "handwritten", "box": BOX}
    vision = Scripted(page=page_json(first_name=same, last_name=same))
    verify = Scripted(verify={FieldKey.first_name: ["יוסף"], FieldKey.last_name: ["יוסף"]})
    [outcome] = read_batch_vision(
        [raw], services=services(settings, storage, imaging, vision, verify)
    )

    assert outcome is not None
    for key in (FieldKey.first_name, FieldKey.last_name):
        assert outcome.results[key].level is ConfidenceLevel.D
        assert outcome.results[key].value == "יוסף"


def test_several_commitments_become_separate_rows(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    items = [
        {"value": v, "source": "screen", "box": [500, 100, 520, 300]}
        for v in ("260005102632", "260005102611", "260005102644")
    ]
    vision = Scripted(page=page_json(commitment_number=items))
    verify = Scripted(page=page_json(commitment_number=items))  # the second reader sees all three
    svc = services(settings, storage, imaging, vision, verify)
    svc = VisionServices(**{**svc.__dict__, "settings": settings})  # whole-page second read
    [outcome] = read_batch_vision([raw], services=svc)

    assert outcome is not None
    rows, _, _ = assemble_rows([outcome], ctx=RowContext(clock=SystemClock()))
    assert len(rows) == 3


@dataclass
class Learned:
    """A learning store holding one statistic."""

    stats: list[FieldStat]

    def field_stats(self) -> list[FieldStat]:
        return self.stats

    def label_synonyms(self, sender: str, field: str) -> list[str]:
        return []

    def pattern_hints(self, sender: str, field: str) -> list[object]:
        return []


def test_a_field_staff_often_corrected_is_not_trusted_on_one_reading(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    stat = FieldStat(sender="מאוחדת", field=FieldKey.last_name, confirmed=2, corrected=3)
    svc = services(settings, storage, imaging, Scripted(), Scripted())
    svc = VisionServices(**{**svc.__dict__, "learning": Learned([stat])})
    [outcome] = read_batch_vision([raw], services=svc)

    assert outcome is not None
    last = outcome.results[FieldKey.last_name]
    assert last.level is ConfidenceLevel.D
    assert last.reason == REASON_TEXT["often_corrected"]
    assert last.value == "ישראלי"  # still shown for the technician


def test_a_field_staff_confirmed_is_left_as_read(
    raw: PageRaw, settings: Settings, storage: TmpdirStorage, imaging: CvImaging
) -> None:
    stat = FieldStat(sender="מאוחדת", field=FieldKey.last_name, confirmed=9, corrected=0)
    vision = Scripted()
    svc = services(settings, storage, imaging, vision, Scripted())
    svc = VisionServices(**{**svc.__dict__, "learning": Learned([stat])})
    [outcome] = read_batch_vision([raw], services=svc)

    assert outcome is not None
    assert outcome.results[FieldKey.last_name].level is ConfidenceLevel.C
    assert any("reviewed 9x" in line for line in vision.requests[0].label_synonyms)


def test_the_store_counts_outcomes_without_values(tmp_path: Path) -> None:
    from backend.adapters.learning.sqlite_store import SqliteLearningStore

    store = SqliteLearningStore(settings=Settings(_env_file=None, data_dir=str(tmp_path)))  # type: ignore[call-arg]
    for corrected in (True, True, False):
        store.record_outcome("מאוחדת", "national_id", corrected=corrected)
    [stat] = store.field_stats()
    assert (stat.confirmed, stat.corrected) == (1, 2)
    assert "011223344" not in stat.model_dump_json()
