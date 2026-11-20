"""La evaluación del reconocimiento facial: umbrales, pliegues, TAR a un FAR fijo, ROC,
rank-1, conjunto abierto y la galería del gestor, con puntajes y embeddings inventados."""

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from benchmarks.common import Report
from benchmarks.faces_evaluation.embeddings import LfwEmbeddings
from benchmarks.faces_evaluation.metrics import (
    accuracy_at,
    best_threshold,
    cross_validate,
    open_set_errors,
    operating_point,
    rank_one,
    roc_curve,
    tar_at_far,
    threshold_at_far,
)
from benchmarks.faces_evaluation_benchmark import (
    IDENTIFICATION,
    MISSING_FACE_SCORE,
    NUMPY_EXACT,
    OPEN_SET,
    SQL_EXACT,
    SQL_HNSW,
    add_identification,
    all_pair_scores,
    identification_split,
    protocol_scores,
)

from faces.detection import BoundingBox, DetectedFace, FaceLandmarks, Point, center_face
from faces.lfw import Fold, ImageReference, Pair

SEPARABLE_SCORES = np.array([0.1, 0.2, 0.3, 0.6, 0.7, 0.8])
SEPARABLE_SAME = np.array([False, False, False, True, True, True])


def test_the_best_threshold_falls_in_the_middle_of_the_gap():
    threshold, accuracy = best_threshold(SEPARABLE_SCORES, SEPARABLE_SAME)
    assert threshold == pytest.approx(0.45)
    assert accuracy == 1.0


def test_the_best_threshold_never_splits_tied_scores():
    scores = np.array([0.5, 0.5, 0.5, 0.9])
    same = np.array([False, True, True, True])
    threshold, accuracy = best_threshold(scores, same)
    assert threshold == 0.5
    assert accuracy == 0.75


def test_when_every_pair_is_different_the_threshold_rejects_them_all():
    scores = np.array([0.2, 0.4])
    threshold, accuracy = best_threshold(scores, np.array([False, False]))
    assert threshold > 0.4
    assert accuracy == 1.0
    assert accuracy_at(scores, np.array([False, False]), threshold) == 1.0


def test_no_pairs_no_threshold():
    with pytest.raises(ValueError, match="no hay pares"):
        best_threshold(np.array([]), np.array([], dtype=bool))


def test_each_fold_is_tested_with_a_threshold_chosen_on_the_others():
    """Cada pliegue ve un umbral distinto: en el 2, los otros eligen 0,45 y su par de la
    misma persona de 0,35 queda abajo; en el 3, eligen 0,275 y su 0,3 de personas
    distintas queda arriba."""
    scores = np.array([0.1, 0.6, 0.2, 0.35, 0.3, 0.7])
    same = np.array([False, True, False, True, False, True])
    folds = np.array([1, 1, 2, 2, 3, 3])
    results = cross_validate(scores, same, folds)
    assert [result.fold for result in results] == [1, 2, 3]
    assert [result.test_accuracy for result in results] == [1.0, 0.5, 0.5]
    assert results[1].threshold == pytest.approx(0.45)
    assert results[2].threshold == pytest.approx(0.275)


def test_the_threshold_at_a_far_accepts_at_most_that_share_of_impostors():
    impostors = np.arange(1000) / 1000.0
    threshold = threshold_at_far(impostors, 0.01)
    assert np.count_nonzero(impostors >= threshold) == 10
    assert threshold_at_far(impostors, 0.0001) > impostors.max()


def test_tar_at_far_counts_the_genuine_pairs_above_that_threshold():
    impostors = np.array([0.1, 0.2, 0.3, 0.9])
    genuine = np.array([0.5, 0.95, 0.99, 0.2])
    point = tar_at_far(genuine, impostors, 0.25)
    assert (point.accepted_impostors, point.far) == (1, 0.25)
    assert point.tar == 0.75


def test_ties_at_the_threshold_keep_the_far_below_the_target():
    impostors = np.array([0.5, 0.5, 0.5, 0.1])
    point = tar_at_far(np.array([0.6]), impostors, 0.5)
    assert point.accepted_impostors == 0
    assert point.tar == 1.0


def test_the_roc_matches_an_operating_point_per_threshold():
    genuine = np.array([0.4, 0.6, 0.8, 0.8])
    impostors = np.array([0.0, 0.2, 0.4, 0.6])
    thresholds = (0.0, 0.4, 0.5, 0.8, 1.0)
    assert roc_curve(genuine, impostors, thresholds) == [
        operating_point(genuine, impostors, threshold) for threshold in thresholds
    ]


def test_rank_one_is_the_share_of_probes_whose_nearest_is_themselves():
    assert rank_one(["a", "b", "c", "a"], ["a", "b", "a", "a"]) == 0.75
    with pytest.raises(ValueError):
        rank_one(["a"], ["a", "b"])


def test_open_set_misses_wrong_identities_and_low_scores():
    errors = open_set_errors(
        genuine_correct=np.array([True, True, False, True]),
        genuine_scores=np.array([0.9, 0.3, 0.9, 0.6]),
        impostor_scores=np.array([0.2, 0.55, 0.1, 0.7]),
        threshold=0.5,
    )
    assert errors.fnir == 0.5
    assert errors.fpir == 0.5


def face_at(x: float, y: float, side: float) -> DetectedFace:
    point = Point(x, y)
    landmarks = FaceLandmarks(point, point, point, point, point)
    return DetectedFace(box=BoundingBox(x, y, side, side), landmarks=landmarks, score=0.99)


def test_the_center_face_wins_over_a_bigger_one_at_the_edge():
    big_at_the_edge = face_at(0, 0, 120)
    centered = face_at(100, 100, 50)
    assert center_face([big_at_the_edge, centered], 250, 250) is centered
    with pytest.raises(ValueError):
        center_face([], 250, 250)


def embeddings_of(people: dict[str, list[list[float]]]) -> LfwEmbeddings:
    names = tuple(
        f"{p}/{p}_{n:04d}.jpg" for p, rows in people.items() for n in range(1, len(rows) + 1)
    )
    raw = np.array([row for rows in people.values() for row in rows], dtype=np.float32)
    vectors = raw / np.linalg.norm(raw, axis=1, keepdims=True)
    count = len(names)
    return LfwEmbeddings(
        names=names,
        people=tuple(name.split("/")[0] for name in names),
        vectors=vectors.astype(np.float32),
        found=np.ones(count, dtype=bool),
        faces=np.ones(count, dtype=int),
        milliseconds=np.ones((count, 4)),
        rows={name: row for row, name in enumerate(names)},
    )


PEOPLE = {
    "Ana": [[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0.8, 0.2, 0, 0]],
    "Beto": [[0, 1, 0, 0], [0.1, 0.9, 0, 0]],
    "Cira": [[0, 0, 1, 0]],
    "Dino": [[0, 0, 0.2, 1]],
}


def test_protocol_pairs_are_scored_by_cosine_and_a_missing_face_is_rejected():
    embeddings = embeddings_of(PEOPLE)
    missing = replace(embeddings, found=np.array([True, True, True, True, False, True, True]))
    fold = Fold(
        matched=(Pair(ImageReference("Ana", 1), ImageReference("Ana", 2), True),),
        mismatched=(Pair(ImageReference("Ana", 1), ImageReference("Beto", 1), False),),
    )
    scored = protocol_scores(missing, (fold, fold))
    assert scored.scores[0] == pytest.approx(float(embeddings.vectors[0] @ embeddings.vectors[1]))
    assert scored.scores[1] == pytest.approx(0.0)
    assert list(scored.folds) == [1, 1, 2, 2]
    assert list(scored.same) == [True, False, True, False]
    assert scored.missing == 0
    beto_missing = Fold(
        matched=(Pair(ImageReference("Beto", 1), ImageReference("Beto", 2), True),),
        mismatched=(),
    )
    assert protocol_scores(missing, (beto_missing,)).scores[0] == MISSING_FACE_SCORE


def test_all_pairs_are_every_same_person_pair_and_one_photo_per_person_against_the_rest():
    genuine, impostors = all_pair_scores(embeddings_of(PEOPLE))
    assert genuine.size == 3 + 1
    assert impostors.size == 4 * 3 // 2


def test_the_gallery_keeps_the_first_photo_of_people_with_two_or_more():
    split = identification_split(embeddings_of(PEOPLE))
    assert len(split.gallery) == 2
    assert list(split.probe_people) == [0, 0, 1]
    assert len(split.strangers) == 2


def test_identification_through_the_engine_agrees_with_numpy(tmp_path: Path):
    report = Report("prueba", {})
    arguments = argparse.Namespace(thresholds=[0.5])
    add_identification(report, identification_split(embeddings_of(PEOPLE)), arguments)
    found = {(item.technique, item.operation): item.extra for item in report.measurements}
    for technique in (SQL_HNSW, SQL_EXACT, NUMPY_EXACT):
        assert found[(technique, IDENTIFICATION)]["rank_1"] == 1.0
        assert found[(technique, IDENTIFICATION)]["acuerdo_con_exacta"] == 1.0
        open_set = found[(f"{technique}: umbral 0.5", OPEN_SET)]
        assert (open_set["fnir"], open_set["fpir"]) == (0.0, 0.0)
