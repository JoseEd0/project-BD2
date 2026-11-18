"""Los fragmentos temporales de W3C Media Fragments en la ruta de un audio: qué se lee, qué
se rechaza y cómo se vuelve a escribir."""

from pathlib import Path

import pytest

from multimedia import (
    EmptyFragmentError,
    FragmentOnImageError,
    MalformedFragmentError,
    MediaReference,
    TemporalFragment,
    UnsupportedMediaTypeError,
    media_reference,
    parse_fragment,
    split_fragment,
)


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("t=10,15", TemporalFragment(10.0, 15.0)),
        ("t=10", TemporalFragment(10.0, None)),
        ("t=,15", TemporalFragment(0.0, 15.0)),
        ("t=npt:10,15", TemporalFragment(10.0, 15.0)),
        ("t=npt:,15", TemporalFragment(0.0, 15.0)),
        ("t=0.5,2.25", TemporalFragment(0.5, 2.25)),
        ("t=10.,20", TemporalFragment(10.0, 20.0)),
        ("t=01:30,02:00.5", TemporalFragment(90.0, 120.5)),
        ("t=1:02:03,1:02:04", TemporalFragment(3723.0, 3724.0)),
    ],
)
def test_the_npt_forms_are_read(fragment: str, expected: TemporalFragment):
    assert parse_fragment(fragment) == expected


@pytest.mark.parametrize(
    "fragment",
    [
        "t=",
        "t=,",
        "t=diez,15",
        "t=-5,10",
        "t=10,15,20",
        "t=1:5,10",
        "t=01:75,02:00",
        "t=smpte:00:00:10:00,00:00:15:00",
        "t=clock:2026-11-18T10:00:00Z",
        "xywh=160,120,320,240",
        "track=audio",
        "t=10,15&xywh=0,0,10,10",
        "",
    ],
)
def test_what_is_not_an_npt_range_is_malformed(fragment: str):
    with pytest.raises(MalformedFragmentError):
        parse_fragment(fragment)


@pytest.mark.parametrize("fragment", ["t=15,10", "t=10,10", "t=,0"])
def test_an_empty_range_is_rejected(fragment: str):
    with pytest.raises(EmptyFragmentError, match="vacío"):
        parse_fragment(fragment)


def test_a_path_without_a_hash_has_no_fragment():
    assert split_fragment("data/pistas/123.mp3") == ("data/pistas/123.mp3", None)


def test_the_fragment_is_split_from_the_path():
    assert split_fragment("data/pistas/123.mp3#t=10,20") == (
        "data/pistas/123.mp3",
        TemporalFragment(10.0, 20.0),
    )


@pytest.mark.parametrize(
    ("fragment", "written"),
    [
        (TemporalFragment(10.0, 15.0), "t=10,15"),
        (TemporalFragment(2.5, None), "t=2.5"),
        (TemporalFragment(0.0, 0.1), "t=0,0.1"),
    ],
)
def test_a_fragment_is_written_back_as_it_is_read(fragment: TemporalFragment, written: str):
    assert str(fragment) == written
    assert parse_fragment(written) == fragment


def test_a_reference_locates_the_path_without_its_fragment(tmp_path: Path):
    located: list[str] = []

    def locate(text: str) -> Path:
        located.append(text)
        return tmp_path / text

    reference = media_reference("pista.mp3#t=5,10", locate)

    assert located == ["pista.mp3"]
    assert reference == MediaReference(tmp_path / "pista.mp3", TemporalFragment(5.0, 10.0))


def test_an_image_has_no_temporal_fragment(tmp_path: Path):
    with pytest.raises(FragmentOnImageError, match="imagen"):
        media_reference("foto.jpg#t=1,2", lambda text: tmp_path / text)


def test_a_fragment_on_an_unknown_type_is_rejected(tmp_path: Path):
    with pytest.raises(UnsupportedMediaTypeError):
        media_reference("notas.txt#t=1,2", lambda text: tmp_path / text)
