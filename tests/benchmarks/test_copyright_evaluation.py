"""Las métricas de la evaluación de copyright sobre casos hechos a mano, y las cifras que salen
de un puñado de subidas."""

from pathlib import Path

import pytest
from benchmarks.common import Report
from benchmarks.remixes.evaluation import (
    ALL_VERSIONS,
    F1,
    LATENCY,
    PRECISION,
    RECALL,
    ROC,
    SCORES,
    UPLOAD_SPAN,
    Scoring,
    Upload,
    add_scoring_comparison,
    analyse,
    length_bin_label,
    load_uploads,
    save_uploads,
)
from benchmarks.remixes.generator import Span
from benchmarks.remixes.metrics import (
    Case,
    Counts,
    Detected,
    RocPoint,
    Truth,
    area_under,
    counts,
    interval_iou,
    roc_curve,
    youden_point,
)

ANYWHERE = Span(0.0, 10.0)
THRESHOLDS = [0.0, 0.25, 0.5, 0.75, 1.0]


def copy(song: int) -> Truth:
    return Truth(song, ANYWHERE, ANYWHERE)


def found(song: int, score: float) -> Detected:
    return Detected(song, score, ANYWHERE, ANYWHERE)


def test_iou_of_spans():
    assert interval_iou(Span(0, 10), Span(0, 10)) == 1.0
    assert interval_iou(Span(0, 10), Span(5, 15)) == pytest.approx(5 / 15)
    assert interval_iou(Span(0, 10), Span(2, 4)) == pytest.approx(0.2)
    assert interval_iou(Span(0, 5), Span(5, 10)) == 0.0
    assert interval_iou(Span(0, 5), Span(7, 10)) == 0.0


def test_counts_hits_misses_and_false_alarms_once_per_song():
    cases = [
        Case((copy(1), copy(2)), (found(1, 0.9), found(1, 0.8), found(3, 0.6), found(2, 0.3))),
        Case((), (found(4, 0.7),)),
        Case((copy(5),), ()),
    ]
    result = counts(cases, 0.5)
    assert result == Counts(true_positives=1, false_positives=2, false_negatives=2)
    assert result.precision == pytest.approx(1 / 3)
    assert result.recall == pytest.approx(1 / 3)
    assert result.f1 == pytest.approx(1 / 3)


def test_precision_recall_and_f1_by_hand():
    result = Counts(true_positives=6, false_positives=2, false_negatives=4)
    assert result.precision == pytest.approx(0.75)
    assert result.recall == pytest.approx(0.6)
    assert result.f1 == pytest.approx(2 * 0.75 * 0.6 / 1.35)


def test_nothing_claimed_gives_zero_precision_and_f1():
    result = Counts(0, 0, 3)
    assert (result.precision, result.recall, result.f1) == (0.0, 0.0, 0.0)


def test_roc_counts_copies_against_uploads_without_copies():
    cases = [
        Case((copy(1),), (found(1, 0.8),)),
        Case((copy(2),), (found(2, 0.4), found(9, 0.9))),
        Case((), (found(3, 0.6),)),
        Case((), ()),
    ]
    curve = roc_curve(cases, THRESHOLDS)
    assert curve == [
        RocPoint(0.0, 1.0, 1.0),
        RocPoint(0.25, 1.0, 0.5),
        RocPoint(0.5, 0.5, 0.5),
        RocPoint(0.75, 0.5, 0.0),
        RocPoint(1.0, 0.0, 0.0),
    ]
    assert youden_point(curve).threshold == 0.75
    assert area_under(curve) == pytest.approx(0.75)


def test_a_perfect_separation_has_area_one_and_a_coin_has_half():
    perfect = [RocPoint(0.5, 1.0, 0.0)]
    coin = [RocPoint(0.5, 0.5, 0.5)]
    assert area_under(perfect) == pytest.approx(1.0)
    assert area_under(coin) == pytest.approx(0.5)


def test_roc_needs_both_kinds_of_upload():
    with pytest.raises(ValueError):
        roc_curve([Case((copy(1),), ())], THRESHOLDS)


def test_youden_breaks_ties_with_the_higher_threshold():
    curve = [RocPoint(0.2, 0.9, 0.4), RocPoint(0.4, 0.7, 0.2), RocPoint(0.6, 0.5, 0.5)]
    assert youden_point(curve).threshold == 0.4


def uploads() -> list[Upload]:
    hit = Detected(1, 0.9, Span(0.0, 7.5), Span(5.0, 12.5))
    noisy = Detected(2, 0.6, Span(0.0, 5.0), Span(0.0, 5.0))
    return [
        Upload(
            0, "original", 10.0, 100.0, Case((Truth(1, Span(0, 10), Span(5, 15)),), (hit,)), (10.0,)
        ),
        Upload(1, "original", 20.0, 100.0, Case((), (noisy,)), ()),
        Upload(0, "ruido", 10.0, 200.0, Case((Truth(1, Span(0, 10), Span(5, 15)),), ()), (10.0,)),
        Upload(1, "ruido", 20.0, 300.0, Case((), ()), ()),
    ]


def measured(report: Report, technique: str, operation: str) -> dict:
    return next(
        item.extra | {"ms": item.milliseconds}
        for item in report.measurements
        if (item.technique, item.operation) == (technique, operation)
    )


def test_the_analysis_chooses_the_youden_threshold_and_measures_at_it():
    report = Report("prueba", {})
    chosen = analyse(report, uploads(), THRESHOLDS, 0.5, (5.0, 7.5, 20.0))
    assert chosen == 0.75
    assert measured(report, ALL_VERSIONS, ROC)["tpr_elegido"] == 0.5
    assert measured(report, ALL_VERSIONS, ROC)["fpr_elegido"] == 0.0
    assert measured(report, PRECISION, "original")["elegido"] == 1.0
    assert measured(report, RECALL, "original")["elegido"] == 1.0
    assert measured(report, PRECISION, "original")["provisional"] == 0.5
    assert measured(report, F1, "ruido")["elegido"] == 0.0
    assert measured(report, UPLOAD_SPAN, "original")["iou"] == 0.75
    assert measured(report, "original", "original")["iou"] == pytest.approx(0.75)
    assert measured(report, LATENCY, "original")["ms"] == pytest.approx(450.0)
    assert measured(report, "original", length_bin_label(7.5, 20.0))["recall"] == 1.0
    assert measured(report, "ruido", length_bin_label(7.5, 20.0))["recall"] == 0.0
    assert measured(report, "ruido", length_bin_label(5.0, 7.5))["fragmentos"] == 0


def test_another_scoring_is_compared_on_the_same_matches():
    report = Report("prueba", {})
    matches = [
        [{"song": {"id": 1}, "score": 0.2, "windows": 3, **_spans()}],
        [{"song": {"id": 2}, "score": 0.6, "windows": 1, **_spans()}],
        [],
        [],
    ]
    by_windows = Scoring("ventanas", lambda match: match["windows"] / 3)
    add_scoring_comparison(report, uploads(), matches, [by_windows], THRESHOLDS)
    pooled = next(
        item.extra
        for item in report.measurements
        if item.operation == SCORES and item.extra["version"] == ALL_VERSIONS
    )
    assert pooled["auc"] == pytest.approx(0.625)
    assert (pooled["tpr"], pooled["fpr"]) == (0.5, 0.0)


def test_uploads_survive_the_cases_file(tmp_path: Path):
    matches = [[{"song": {"id": 1}, "score": 0.9, **_spans()}], [], [], []]
    path = save_uploads(tmp_path / "casos.json", uploads(), matches)
    loaded, raw = load_uploads(path)
    assert raw == matches
    assert [upload.case.truth for upload in loaded] == [upload.case.truth for upload in uploads()]
    assert loaded[0].case.detected[0].song_id == 1


def _spans() -> dict:
    span = {"start_seconds": 0.0, "end_seconds": 7.5}
    return {"upload": span, "original": span}
