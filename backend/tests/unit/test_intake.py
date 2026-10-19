"""Tests for S0 intake (T9).

Safety violations refuse the whole upload; unreadable pages are reported and the batch goes on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.adapters.imaging.pdf import DocumentReader
from backend.adapters.storage.tempdir import TmpdirStorage
from backend.core.errors import (
    ActiveContentPdf,
    EncryptedPdf,
    TooLarge,
    UnsupportedMedia,
)
from backend.core.pipeline.s0_intake import IntakeResult, UploadedBlob, intake
from backend.settings import Settings
from backend.tests.fixtures.synthetic import files, pages

JOB_ID = "job-intake"


def _run(
    uploads: list[UploadedBlob],
    settings: Settings,
    documents: DocumentReader,
    storage: TmpdirStorage,
    **kwargs: object,
) -> IntakeResult:
    return intake(
        uploads,
        settings=settings,
        documents=documents,
        storage=storage,
        job_id=JOB_ID,
        **kwargs,  # type: ignore[arg-type]
    )


def _small_page() -> bytes:
    return files.png_bytes(pages.clean_page(width=300, height=420))


# ---------------------------------------------------------------- refusals (whole upload)


def test_rejects_png_renamed_to_pdf(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    with pytest.raises(UnsupportedMedia):
        _run([UploadedBlob(filename="scan.pdf", data=_small_page())], settings, documents, storage)


def test_rejects_content_that_is_no_accepted_type(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    with pytest.raises(UnsupportedMedia):
        _run([UploadedBlob(filename="notes", data=b"just some text")], settings, documents, storage)


def test_rejects_empty_file(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    with pytest.raises(UnsupportedMedia):
        _run([UploadedBlob(filename="a.png", data=b"")], settings, documents, storage)


def test_rejects_empty_upload(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    with pytest.raises(UnsupportedMedia):
        _run([], settings, documents, storage)


def test_content_decides_when_extension_is_unknown(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    result = _run(
        [UploadedBlob(filename="scan.bin", data=_small_page())], settings, documents, storage
    )
    assert len(result.pages) == 1


def test_rejects_encrypted_pdf(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    with pytest.raises(EncryptedPdf):
        _run(
            [UploadedBlob(filename="a.pdf", data=files.encrypted_pdf())],
            settings,
            documents,
            storage,
        )


def test_rejects_pdf_with_javascript(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    with pytest.raises(ActiveContentPdf):
        _run(
            [UploadedBlob(filename="a.pdf", data=files.pdf_with_javascript())],
            settings,
            documents,
            storage,
        )


@pytest.mark.parametrize(
    "build", [files.pdf_with_open_action, files.pdf_with_attachment], ids=["launch", "attachment"]
)
def test_rejects_pdf_with_other_active_content(
    build: object, settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    assert callable(build)
    with pytest.raises(ActiveContentPdf):
        _run([UploadedBlob(filename="a.pdf", data=build())], settings, documents, storage)


def test_rejects_zip_bomb(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    with pytest.raises(TooLarge):
        _run([UploadedBlob(filename="b.zip", data=files.zip_bomb())], settings, documents, storage)


@pytest.mark.parametrize(
    "member",
    ["../evil.png", "/abs.png", "C:/drive.png", "..\\evil.png", "ok/../../evil.png"],
)
def test_rejects_zip_path_traversal(
    member: str, settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    archive = files.zip_of({member: _small_page()})
    with pytest.raises(UnsupportedMedia):
        _run([UploadedBlob(filename="t.zip", data=archive)], settings, documents, storage)


def test_rejects_nested_zip(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    inner = files.zip_of({"a.png": _small_page()})
    with pytest.raises(UnsupportedMedia):
        _run(
            [UploadedBlob(filename="o.zip", data=files.zip_of({"inner.zip": inner}))],
            settings,
            documents,
            storage,
        )


def test_rejects_oversized_upload(tmp_path: Path) -> None:
    small = Settings(_env_file=None, data_dir=str(tmp_path), max_upload_mb=1)  # type: ignore[call-arg]
    data = b"%PDF-1.4\n" + bytes(2 * 1024 * 1024)
    with pytest.raises(TooLarge):
        _run(
            [UploadedBlob(filename="big.pdf", data=data)],
            small,
            DocumentReader(settings=small),
            TmpdirStorage(settings=small),
        )


def test_rejects_too_many_pages(tmp_path: Path) -> None:
    few = Settings(_env_file=None, data_dir=str(tmp_path), max_pages=2)  # type: ignore[call-arg]
    three = files.pdf_of_images([pages.clean_page(width=300, height=420)] * 3)
    with pytest.raises(TooLarge):
        _run(
            [UploadedBlob(filename="three.pdf", data=three)],
            few,
            DocumentReader(settings=few),
            TmpdirStorage(settings=few),
        )


# ---------------------------------------------------------------- accepted input


def test_same_page_twice_has_same_hash(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    page = _small_page()
    result = _run(
        [UploadedBlob(filename="a.png", data=page), UploadedBlob(filename="b.png", data=page)],
        settings,
        documents,
        storage,
    )
    first, second = result.pages
    assert first.sha256 == second.sha256 == first.image.sha256
    assert first.page_id != second.page_id


def test_one_bad_page_does_not_abort_batch(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    # The middle page is far larger than the pixel budget, so only it fails to render.
    pdf = files.pdf_with_page_sizes([(200, 200), (14000, 14000), (200, 200)])
    result = _run([UploadedBlob(filename="doc.pdf", data=pdf)], settings, documents, storage)

    assert [p.page_id for p in result.pages] == ["doc#1", "doc#3"]
    assert [(f.page_index, f.code, f.recoverable) for f in result.failures] == [
        (1, "RENDER_FAILED", True)
    ]


def test_corrupt_image_is_reported_not_raised(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    result = _run(
        [
            UploadedBlob(filename="bad.png", data=files.corrupt_png()),
            UploadedBlob(filename="good.png", data=_small_page()),
        ],
        settings,
        documents,
        storage,
    )
    assert [p.page_id for p in result.pages] == ["good#1"]
    assert [f.source_file for f in result.failures] == ["bad.png"]
    assert result.failures[0].page_index == 0


def test_pages_keep_upload_order_across_pdf_png_and_zip(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    pdf = files.pdf_of_images([pages.clean_page(width=300, height=420)] * 2)
    archive = files.zip_of(
        {"z1.png": _small_page(), "z2.jpg": files.jpeg_bytes(pages.clean_page())}
    )
    result = _run(
        [
            UploadedBlob(filename="a.pdf", data=pdf),
            UploadedBlob(filename="b.zip", data=archive),
            UploadedBlob(filename="c.png", data=_small_page()),
        ],
        settings,
        documents,
        storage,
    )
    assert [p.page_id for p in result.pages] == ["a#1", "a#2", "z1#1", "z2#1", "c#1"]
    assert [p.page_index for p in result.pages] == [0, 1, 0, 0, 0]


def test_archive_junk_members_are_skipped(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    archive = files.zip_of(
        {
            "scan.png": _small_page(),
            "__MACOSX/._scan.png": b"junk",
            ".DS_Store": b"junk",
            "Thumbs.db": b"junk",
            "folder/": b"",
        }
    )
    result = _run([UploadedBlob(filename="a.zip", data=archive)], settings, documents, storage)
    assert [p.page_id for p in result.pages] == ["scan#1"]


def test_repeated_file_names_get_unique_page_ids(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    page = _small_page()
    result = _run(
        [UploadedBlob(filename=f"d{n}/scan.png", data=page) for n in range(3)],
        settings,
        documents,
        storage,
    )
    assert [p.page_id for p in result.pages] == ["scan#1", "scan~2#1", "scan~3#1"]


def test_hostile_file_name_cannot_leave_the_job_directory(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage, tmp_path: Path
) -> None:
    result = _run(
        [UploadedBlob(filename="../../etc/passwd.png", data=_small_page())],
        settings,
        documents,
        storage,
    )
    page = result.pages[0]
    assert page.page_id == "passwd#1"
    assert Path(page.image.path).resolve().is_relative_to(tmp_path.resolve())


def test_known_sample_flag_follows_the_hash_list(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    blob = UploadedBlob(filename="a.png", data=_small_page())
    unknown = _run([blob], settings, documents, storage).pages[0]
    assert unknown.known_sample is False  # the safe default: processed local-only

    known = _run([blob], settings, documents, storage, known_hashes=frozenset({unknown.sha256}))
    assert known.pages[0].known_sample is True


def test_rendered_pdf_page_has_expected_size_for_dpi(
    settings: Settings, documents: DocumentReader, storage: TmpdirStorage
) -> None:
    pdf = files.pdf_with_page_sizes([(72, 144)])  # 1 x 2 inches
    page = _run([UploadedBlob(filename="s.pdf", data=pdf)], settings, documents, storage).pages[0]
    assert (page.image.width, page.image.height) == (settings.render_dpi, 2 * settings.render_dpi)
