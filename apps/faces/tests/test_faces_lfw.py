"""LFW sobre un LFW de juguete: archivos de pares, extracción y comprobación de lo bajado."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from appkit.download import RemoteFile
from faces import DatasetError
from faces import lfw as lfw_module
from faces.lfw import (
    ARCHIVE,
    ARCHIVE_FILENAME,
    PAIR_FILES,
    ImageReference,
    LfwImage,
    check_complete,
    download_lfw,
    lfw_images,
    read_pairs,
)

PHOTOS = (
    "Ana_Diaz/Ana_Diaz_0001.jpg",
    "Ana_Diaz/Ana_Diaz_0002.jpg",
    "Beto_Ruiz/Beto_Ruiz_0001.jpg",
)
TWO_FOLDS = (
    "2\t1\nAna_Diaz\t1\t2\nAna_Diaz\t1\tBeto_Ruiz\t1\nAna_Diaz\t2\t1\nBeto_Ruiz\t1\tAna_Diaz\t2\n"
)
ONE_SET = "1\nAna_Diaz\t1\t2\nBeto_Ruiz\t1\tAna_Diaz\t1\n"


def lfw_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name in PHOTOS:
            member = tarfile.TarInfo(f"lfw/{name}")
            member.size = len(b"jpeg")
            bundle.addfile(member, io.BytesIO(b"jpeg"))
    return buffer.getvalue()


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def toy_lfw(monkeypatch: pytest.MonkeyPatch) -> list[RemoteFile]:
    """`download_lfw` sobre un LFW de tres fotos de dos personas, servido sin red. Devuelve
    la lista de lo que se pidió bajar."""
    contents = {
        ARCHIVE: lfw_archive(),
        **{remote: TWO_FOLDS.encode() for remote in PAIR_FILES.values()},
    }
    requested: list[RemoteFile] = []

    def serve(remote: RemoteFile, destination: Path) -> Path:
        requested.append(remote)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents[remote])
        return destination

    monkeypatch.setattr(lfw_module, "fetch_verified", serve)
    monkeypatch.setattr(lfw_module, "LFW_IMAGES", len(PHOTOS))
    monkeypatch.setattr(lfw_module, "LFW_PEOPLE", 2)
    return requested


def test_pairs_txt_has_its_folds_of_matched_then_mismatched_pairs(tmp_path: Path):
    folds = read_pairs(write(tmp_path / "pairs.txt", TWO_FOLDS))

    assert len(folds) == 2
    first = folds[0]
    assert [pair.same_person for pair in (*first.matched, *first.mismatched)] == [True, False]
    assert first.matched[0].first == ImageReference("Ana_Diaz", 1)
    assert first.mismatched[0].second == ImageReference("Beto_Ruiz", 1)


def test_a_view_one_file_is_a_single_set(tmp_path: Path):
    (fold,) = read_pairs(write(tmp_path / "pairsDevTrain.txt", ONE_SET))

    assert len(fold.matched) == len(fold.mismatched) == 1


def test_a_file_shorter_than_its_header_is_rejected(tmp_path: Path):
    with pytest.raises(DatasetError, match="termina antes"):
        read_pairs(write(tmp_path / "pairs.txt", "3\t1\n" + TWO_FOLDS.split("\n", 1)[1]))


def test_a_line_that_is_not_a_pair_is_rejected(tmp_path: Path):
    with pytest.raises(DatasetError, match="no es un par"):
        read_pairs(write(tmp_path / "pairs.txt", "1\nAna_Diaz\n"))


def test_pairs_out_of_protocol_order_are_rejected(tmp_path: Path):
    swapped = "1\nAna_Diaz\t1\tBeto_Ruiz\t1\nAna_Diaz\t1\t2\n"

    with pytest.raises(DatasetError, match="orden"):
        read_pairs(write(tmp_path / "pairs.txt", swapped))


def test_a_reference_names_its_file_with_four_digits():
    assert ImageReference("Ana_Diaz", 7).path_in(Path("lfw")) == Path(
        "lfw/Ana_Diaz/Ana_Diaz_0007.jpg"
    )


def test_downloading_extracts_the_photos_and_drops_the_archive(
    toy_lfw: list[RemoteFile], tmp_path: Path
):
    images_directory = download_lfw(tmp_path)

    assert lfw_images(images_directory) == [
        LfwImage(person=name.split("/")[0], path=images_directory / name) for name in PHOTOS
    ]
    assert not (tmp_path / ARCHIVE_FILENAME).exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(["lfw", *PAIR_FILES])


def test_a_second_download_does_not_fetch_the_archive_again(
    toy_lfw: list[RemoteFile], tmp_path: Path
):
    download_lfw(tmp_path)
    toy_lfw.clear()

    download_lfw(tmp_path)

    assert ARCHIVE not in toy_lfw


def test_a_half_extracted_attempt_is_discarded(toy_lfw: list[RemoteFile], tmp_path: Path):
    leftover = tmp_path / lfw_module.EXTRACTION_DIRECTORY / "lfw" / "Medio_Hecho"
    leftover.mkdir(parents=True)

    images_directory = download_lfw(tmp_path)

    assert not (images_directory / "Medio_Hecho").exists()
    assert not leftover.exists()


def test_a_photo_missing_from_the_gallery_is_detected(toy_lfw: list[RemoteFile], tmp_path: Path):
    images_directory = download_lfw(tmp_path)
    (images_directory / PHOTOS[-1]).unlink()

    with pytest.raises(DatasetError, match="2 fotos de 1 personas"):
        check_complete(tmp_path)


def test_a_pair_naming_a_photo_that_is_not_there_is_detected(
    toy_lfw: list[RemoteFile], tmp_path: Path
):
    download_lfw(tmp_path)
    write(tmp_path / "pairs.txt", "1\t1\nAna_Diaz\t1\t3\nAna_Diaz\t1\tBeto_Ruiz\t1\n")

    with pytest.raises(DatasetError, match="Ana_Diaz_0003"):
        check_complete(tmp_path)
