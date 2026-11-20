"""Las personas de LFW de «¿A qué famoso te pareces?», sin modelos ni gestor."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import numpy as np
import pytest

from faces.famous import display_name, famous_matches, famous_people, lfw_images_directory
from faces.lfw import LfwImage
from faces.recognition import Embedding

ROOT = Path("/datasets/lfw/lfw")


def unit(*values: float) -> Embedding:
    vector = np.zeros(128, dtype=np.float32)
    vector[: len(values)] = values
    return np.asarray(vector / np.linalg.norm(vector), dtype=np.float32)


def image(person: str, number: int) -> LfwImage:
    return LfwImage(person, ROOT / person / f"{person}_{number:04d}.jpg")


def test_lfw_photos_live_under_the_dataset_folder():
    assert lfw_images_directory(Path("/datasets")) == ROOT


def test_names_are_shown_with_spaces():
    assert display_name("George_W_Bush") == "George W Bush"


def test_one_person_per_folder_with_the_photo_closest_to_the_average():
    images = [image("Ann_Lee", 1), image("Ann_Lee", 2), image("Ann_Lee", 3), image("Bo_Li", 1)]
    embeddings = {
        images[0].path: unit(1, 0),
        images[1].path: unit(0.9, 0.1),
        images[2].path: unit(0, 1),
        images[3].path: unit(0, 0, 1),
    }

    ann, bo = famous_people(images, embeddings, ROOT)

    assert (ann.name, ann.photos, bo.name, bo.photos) == ("Ann Lee", 3, "Bo Li", 1)
    assert ann.photo == PurePosixPath("Ann_Lee/Ann_Lee_0002.jpg")
    assert float(np.linalg.norm(ann.embedding)) == pytest.approx(1.0)
    assert ann.row(7)["id"] == 7 and len(ann.row(7)["embedding"]) == 128  # type: ignore[arg-type]


def test_people_without_a_face_in_any_photo_are_left_out():
    images = [image("Ann_Lee", 1), image("Bo_Li", 1)]

    people = famous_people(images, {images[1].path: unit(1)}, ROOT)

    assert [person.name for person in people] == ["Bo Li"]


def test_no_photos_no_people():
    assert famous_people([], {}, ROOT) == []


def test_matches_go_from_the_most_alike():
    rows = [
        {"id": 2, "nombre": "B", "fotos": 1, "score": 0.2},
        {"id": 1, "nombre": "A", "fotos": 3, "score": 0.6},
    ]

    assert [match.name for match in famous_matches(rows)] == ["A", "B"]
    assert famous_matches([]) == []
