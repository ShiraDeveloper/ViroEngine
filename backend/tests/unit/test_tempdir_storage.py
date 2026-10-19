"""Tests for the TmpdirStorage adapter (T9)."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest

from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.models import ImageRef
from backend.tests.fixtures.synthetic import files, pages

PNG = files.png_bytes(pages.clean_page(width=120, height=160))


def test_put_then_get_round_trips_and_records_size(storage: TmpdirStorage) -> None:
    ref = storage.put_page_image("job-1", "scan#1", PNG, "color")
    assert storage.get(ref) == PNG
    assert (ref.width, ref.height) == (120, 160)
    assert Path(ref.path).is_absolute()


def test_identical_bytes_give_the_same_reference(storage: TmpdirStorage) -> None:
    assert storage.put_page_image("job-1", "p#1", PNG, "color") == storage.put_page_image(
        "job-1", "p#1", PNG, "color"
    )


def test_different_variants_of_one_page_coexist(storage: TmpdirStorage) -> None:
    raw = storage.put_page_image("job-1", "p#1", PNG, "color")
    other = storage.put_page_image(
        "job-1", "p#1", files.png_bytes(pages.clean_page(seed=3)), "color"
    )
    assert raw.path != other.path
    assert storage.get(raw) == PNG  # the raw page survives a corrected copy being stored


def test_page_ids_with_odd_characters_stay_inside_the_job(
    storage: TmpdirStorage, tmp_path: Path
) -> None:
    ref = storage.put_page_image("job-1", "../../x/..\\y#1", PNG, "thumb")
    assert Path(ref.path).resolve().is_relative_to((tmp_path / "jobs" / "job-1").resolve())


@pytest.mark.parametrize("job_id", ["", "../x", "a/b", "a\\b", ".hidden", "x" * 65])
def test_malformed_job_id_is_rejected(storage: TmpdirStorage, job_id: str) -> None:
    with pytest.raises(ValueError, match="job id"):
        storage.put_page_image(job_id, "p#1", PNG, "color")


def test_get_refuses_a_path_outside_the_storage_root(
    storage: TmpdirStorage, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.png"
    outside.write_bytes(PNG)
    forged = ImageRef(
        job_id="job-1",
        page_id="p#1",
        kind="color",
        path=str(outside),
        width=1,
        height=1,
        sha256="0" * 64,
    )
    assert storage.get(forged) == b""


def test_get_returns_empty_bytes_for_a_missing_file(storage: TmpdirStorage) -> None:
    ref = storage.put_page_image("job-1", "p#1", PNG, "color")
    Path(ref.path).unlink()
    assert storage.get(ref) == b""


def test_purge_removes_the_job_and_is_idempotent(storage: TmpdirStorage) -> None:
    first = storage.put_page_image("job-1", "p#1", PNG, "color")
    storage.put_page_image("job-1", "p#1", files.png_bytes(pages.clean_page(seed=3)), "thumb")
    keep = storage.put_page_image("job-2", "p#1", PNG, "color")

    assert storage.purge_job("job-1") == 2
    assert storage.get(first) == b""
    assert storage.purge_job("job-1") == 0
    assert storage.get(keep) == PNG  # another job is untouched


@pytest.mark.parametrize("job_id", ["../job-2", "", "unknown"])
def test_purge_of_malformed_or_unknown_id_is_harmless(storage: TmpdirStorage, job_id: str) -> None:
    keep = storage.put_page_image("job-2", "p#1", PNG, "color")
    assert storage.purge_job(job_id) == 0
    assert storage.get(keep) == PNG


def test_unreadable_image_bytes_record_zero_size(storage: TmpdirStorage) -> None:
    ref = storage.put_page_image("job-1", "p#1", b"not an image", "crop")
    assert (ref.width, ref.height) == (0, 0)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_files_and_directories_are_private_to_the_owner(storage: TmpdirStorage) -> None:
    ref = storage.put_page_image("job-1", "p#1", PNG, "color")
    path = Path(ref.path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert not any(name.startswith(".part-") for name in os.listdir(path.parent))
