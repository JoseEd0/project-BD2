"""Speech Commands sin tocar la red: el `.tar.gz` se arma aquí con el formato del dataset y
se sirve como `file://`."""

import csv
import io
import tarfile
from pathlib import Path

import pytest
from benchmarks.multimedia.subsets import QUERIES_CSV, SUBSETS_DIRECTORY

from benchmarks.multimedia import speech_commands as speech

SEED = 5
WORDS = ("yes", "no", "marvin")
SPEAKERS = 12
TAKES = 2


def add_member(tar: tarfile.TarFile, member: str, data: bytes) -> None:
    info = tarfile.TarInfo(member)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def publish_archive(path: Path) -> None:
    """Doce hablantes que dicen cada palabra dos veces, más lo que no es un clip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        add_member(tar, "./LICENSE", b"CC BY 4.0")
        add_member(tar, "./testing_list.txt", b"yes/0000000a_nohash_0.wav\n")
        add_member(tar, "./_background_noise_/white_noise.wav", b"RIFF ruido")
        for speaker in range(SPEAKERS):
            for word in WORDS:
                for take in range(TAKES):
                    add_member(tar, f"./{word}/{speaker:08x}_nohash_{take}.wav", f"RIFF {word}".encode())


def test_a_clip_path_gives_its_word_and_speaker():
    item = speech.clip_item("yes/0a7c2a8d_nohash_3.wav")
    assert item is not None
    assert (item.path, item.category, item.group) == ("clips/yes/0a7c2a8d_nohash_3.wav", "yes", "0a7c2a8d")
    assert item.title == "yes (0a7c2a8d, toma 3)"


@pytest.mark.parametrize(
    "relative",
    ["_background_noise_/white_noise.wav", "LICENSE", "testing_list.txt", "../yes/0a7c2a8d_nohash_3.wav", "yes/notaspeaker_nohash_0.wav"],
)
def test_what_is_not_a_clip_is_ignored(relative: str):
    assert speech.clip_item(relative) is None


def test_only_the_clips_are_extracted_and_the_folder_is_marked_complete(tmp_path: Path):
    archive = tmp_path / speech.ARCHIVE
    publish_archive(archive)
    clips = tmp_path / speech.CLIPS_DIRECTORY
    assert speech.extract_clips(archive, clips) == SPEAKERS * len(WORDS) * TAKES
    assert (clips / speech.COMPLETE_MARKER).exists()
    assert not (clips / "_background_noise_").exists()
    assert (clips / "marvin" / "0000000b_nohash_1.wav").read_bytes() == b"RIFF marvin"
    assert len(list(speech.read_clips(clips))) == SPEAKERS * len(WORDS) * TAKES


def test_build_keeps_query_speakers_out_of_the_base_and_reuses_the_clips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    remote = tmp_path / "remoto" / speech.ARCHIVE
    publish_archive(remote)
    output = tmp_path / "speech"
    written = speech.build(output, "data/datasets/speech", [6, 30], 2, SEED, remote.as_uri())
    assert written == SPEAKERS * len(WORDS) * TAKES

    def read(path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    queries = read(output / QUERIES_CSV)
    base = read(output / SUBSETS_DIRECTORY / "30" / speech.CLIPS_CSV)
    assert len(queries) == 2 and len(base) == 30
    assert read(output / SUBSETS_DIRECTORY / "6" / speech.CLIPS_CSV) == base[:6]
    assert {row["hablante"] for row in queries}.isdisjoint(row["hablante"] for row in base)
    assert {row["palabra"] for row in base[:3]} == set(WORDS)
    assert base[0]["ruta"].startswith("data/datasets/speech/clips/")
    assert "| consultas | 2 |" in capsys.readouterr().out

    remote.unlink()
    (output / speech.ARCHIVE).unlink()
    assert speech.build(output, "data/datasets/speech", [6, 30], 2, SEED, remote.as_uri()) == 0
    assert read(output / SUBSETS_DIRECTORY / "30" / speech.CLIPS_CSV) == base
