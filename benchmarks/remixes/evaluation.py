"""De las detecciones crudas de cada subida a las cifras del experimento: curva ROC, umbral,
precisión, recall y F1 por distorsión, IoU de los tramos, recall por largo del fragmento y
latencia por minuto de audio.

Se separa de la medida para poder recalcularlo todo desde el JSON de casos sin el gestor.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from benchmarks.common import Report
from benchmarks.remixes.generator import Span
from benchmarks.remixes.metrics import (
    Case,
    Detected,
    RocPoint,
    Truth,
    area_under,
    best_match,
    counts,
    interval_iou,
    roc_curve,
    youden_point,
)

ALL_VERSIONS = "todas"
ROC = "roc"
DETECTION = "deteccion"
LOCALIZATION = "localizacion"
BY_LENGTH = "por_largo"
LATENCY = "latencia"
SCORES = "puntajes"
PRECISION = "precisión"
RECALL = "recall"
F1 = "F1"
UPLOAD_SPAN = "subida"
ORIGINAL_SPAN = "original"
CHOSEN = "elegido"
PROVISIONAL = "provisional"
THRESHOLDS = "umbrales"
TRUE_POSITIVE_RATE = "tpr"
FALSE_POSITIVE_RATE = "fpr"
AUC = "auc"
IOU = "iou"
DIGITS = 3
SECONDS_PER_MINUTE = 60.0
LATENCY_PERCENTILES = (50, 95)


@dataclass(frozen=True, slots=True)
class Upload:
    """Una subida medida: qué remix y con qué distorsión, cuánto duró, cuánto tardó la
    detección y lo que devolvió. `lengths` son los segundos de cada fragmento en el remix sin
    distorsionar, en el orden de `case.truth`."""

    remix: int
    distortion: str
    seconds: float
    milliseconds: float
    case: Case
    lengths: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Scoring:
    """Una definición del puntaje de una coincidencia, para comparar en la curva ROC."""

    name: str
    score: Callable[[dict[str, Any]], float]


def match_of(match: dict[str, Any], score: float | None = None) -> Detected:
    """Una coincidencia de `POST /detections`, con su puntaje o con otro."""
    return Detected(
        int(match["song"]["id"]),
        float(match["score"]) if score is None else score,
        Span(float(match["upload"]["start_seconds"]), float(match["upload"]["end_seconds"])),
        Span(float(match["original"]["start_seconds"]), float(match["original"]["end_seconds"])),
    )


def rescored(upload: Upload, matches: Sequence[dict[str, Any]], scoring: Scoring) -> Case:
    detected = tuple(match_of(match, scoring.score(match)) for match in matches)
    return Case(upload.case.truth, detected)


def analyse(
    report: Report,
    uploads: Sequence[Upload],
    thresholds: Sequence[float],
    provisional: float,
    length_bins: Sequence[float],
) -> float:
    """Suma al informe todas las cifras y devuelve el umbral elegido: el de la máxima J de
    Youden en la curva ROC de todas las versiones juntas."""
    by_version = _by_version(uploads)
    pooled = roc_curve([upload.case for upload in uploads], thresholds)
    chosen = youden_point(pooled).threshold
    report.parameters["umbral_elegido"] = chosen
    for version, group in (*by_version.items(), (ALL_VERSIONS, list(uploads))):
        cases = [upload.case for upload in group]
        _add_roc(report, version, roc_curve(cases, thresholds), chosen, len(group))
        _add_detection(report, version, cases, chosen, provisional)
        _add_localization(report, version, cases, chosen)
        _add_latency(report, version, group)
        _add_recall_by_length(report, version, group, chosen, length_bins)
    return chosen


def add_scoring_comparison(
    report: Report,
    uploads: Sequence[Upload],
    matches: Sequence[Sequence[dict[str, Any]]],
    scorings: Sequence[Scoring],
    thresholds: Sequence[float],
) -> None:
    """El área bajo la curva y el punto de Youden de otras definiciones del puntaje,
    calculadas sobre las mismas coincidencias."""
    everything = list(zip(uploads, matches, strict=True))
    by_version = _by_version(everything, key=lambda pair: pair[0])
    for version, pairs in (*by_version.items(), (ALL_VERSIONS, everything)):
        for scoring in scorings:
            cases = [rescored(upload, found, scoring) for upload, found in pairs]
            curve = roc_curve(cases, thresholds)
            best = youden_point(curve)
            report.add(
                scoring.name,
                SCORES,
                len(pairs),
                0.0,
                version=version,
                auc=round(area_under(curve), DIGITS),
                umbral=best.threshold,
                tpr=round(best.true_positive_rate, DIGITS),
                fpr=round(best.false_positive_rate, DIGITS),
            )


def save_uploads(path: Path, uploads: Iterable[Upload], matches: Iterable[Any]) -> Path:
    """Las subidas y las coincidencias crudas, una por línea dentro de una lista JSON."""
    lines = [
        json.dumps(_upload_record(upload, found), ensure_ascii=False)
        for upload, found in zip(uploads, matches, strict=True)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")
    return path


def load_uploads(path: Path) -> tuple[list[Upload], list[list[dict[str, Any]]]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    uploads = [_upload_of(record) for record in records]
    return uploads, [list(record["coincidencias"]) for record in records]


def _by_version(
    items: Sequence[Any], key: Callable[[Any], Upload] = lambda upload: upload
) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for item in items:
        grouped.setdefault(key(item).distortion, []).append(item)
    return grouped


def _add_roc(
    report: Report, version: str, curve: list[RocPoint], chosen: float, uploads: int
) -> None:
    point = next(point for point in curve if point.threshold == chosen)
    report.add(
        version,
        ROC,
        uploads,
        0.0,
        auc=round(area_under(curve), DIGITS),
        tpr_elegido=round(point.true_positive_rate, DIGITS),
        fpr_elegido=round(point.false_positive_rate, DIGITS),
        umbrales=[point.threshold for point in curve],
        tpr=[round(point.true_positive_rate, DIGITS) for point in curve],
        fpr=[round(point.false_positive_rate, DIGITS) for point in curve],
    )


def _add_detection(
    report: Report, version: str, cases: Sequence[Case], chosen: float, provisional: float
) -> None:
    at_chosen, at_provisional = counts(cases, chosen), counts(cases, provisional)
    for name, measure in ((PRECISION, "precision"), (RECALL, "recall"), (F1, "f1")):
        report.add(
            name,
            version,
            len(cases),
            0.0,
            elegido=round(getattr(at_chosen, measure), DIGITS),
            provisional=round(getattr(at_provisional, measure), DIGITS),
            aciertos=at_chosen.true_positives,
            falsas_alarmas=at_chosen.false_positives,
            fallos=at_chosen.false_negatives,
        )


def _add_localization(report: Report, version: str, cases: Sequence[Case], chosen: float) -> None:
    upload_iou: list[float] = []
    original_iou: list[float] = []
    for case in cases:
        for truth in case.truth:
            match = best_match(case, truth.song_id)
            if match is None or match.score < chosen:
                continue
            upload_iou.append(interval_iou(match.upload, truth.upload))
            original_iou.append(interval_iou(match.original, truth.original))
    for name, values in ((UPLOAD_SPAN, upload_iou), (ORIGINAL_SPAN, original_iou)):
        report.add(
            name,
            version,
            len(cases),
            0.0,
            iou=round(statistics.median(values), DIGITS) if values else None,
            media=round(statistics.fmean(values), DIGITS) if values else None,
            aciertos=len(values),
        )


def _add_latency(report: Report, version: str, uploads: Sequence[Upload]) -> None:
    per_minute = [upload.milliseconds / (upload.seconds / SECONDS_PER_MINUTE) for upload in uploads]
    middle, high = np.percentile(per_minute, LATENCY_PERCENTILES)
    report.add(
        LATENCY,
        version,
        len(uploads),
        round(float(middle), DIGITS),
        p95=round(float(high), DIGITS),
        segundos_medios=round(statistics.fmean(upload.seconds for upload in uploads), DIGITS),
        ms_medios=round(statistics.fmean(upload.milliseconds for upload in uploads), DIGITS),
    )


def _add_recall_by_length(
    report: Report,
    version: str,
    uploads: Sequence[Upload],
    chosen: float,
    bins: Sequence[float],
) -> None:
    found: dict[int, list[bool]] = {index: [] for index in range(len(bins) - 1)}
    for upload in uploads:
        claimed = {match.song_id for match in upload.case.detected if match.score >= chosen}
        for truth, seconds in zip(upload.case.truth, upload.lengths, strict=True):
            index = _bin_of(seconds, bins)
            if index is not None:
                found[index].append(truth.song_id in claimed)
    for index, outcomes in found.items():
        report.add(
            version,
            _bin_label(bins, index),
            len(uploads),
            0.0,
            recall=round(sum(outcomes) / len(outcomes), DIGITS) if outcomes else None,
            fragmentos=len(outcomes),
            desde=bins[index],
            hasta=bins[index + 1],
        )


def length_bin_label(low: float, high: float) -> str:
    return f"{_seconds(low)}-{_seconds(high)} s"


def _bin_label(bins: Sequence[float], index: int) -> str:
    return length_bin_label(bins[index], bins[index + 1])


def _seconds(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _bin_of(seconds: float, bins: Sequence[float]) -> int | None:
    for index in range(len(bins) - 1):
        last = index == len(bins) - 2
        if bins[index] <= seconds < bins[index + 1] or (last and seconds == bins[index + 1]):
            return index
    return None


def _upload_record(upload: Upload, matches: Any) -> dict[str, Any]:
    return {
        "remix": upload.remix,
        "distorsion": upload.distortion,
        "segundos": round(upload.seconds, DIGITS),
        "ms": round(upload.milliseconds, DIGITS),
        "verdad": [
            {
                "cancion": truth.song_id,
                "subida": [round(truth.upload.start, DIGITS), round(truth.upload.end, DIGITS)],
                "original": [
                    round(truth.original.start, DIGITS),
                    round(truth.original.end, DIGITS),
                ],
                "largo": round(seconds, DIGITS),
            }
            for truth, seconds in zip(upload.case.truth, upload.lengths, strict=True)
        ],
        "coincidencias": list(matches),
    }


def _upload_of(record: dict[str, Any]) -> Upload:
    truth = tuple(
        Truth(int(item["cancion"]), Span(*item["subida"]), Span(*item["original"]))
        for item in record["verdad"]
    )
    detected = tuple(match_of(match) for match in record["coincidencias"])
    return Upload(
        int(record["remix"]),
        str(record["distorsion"]),
        float(record["segundos"]),
        float(record["ms"]),
        Case(truth, detected),
        tuple(float(item["largo"]) for item in record["verdad"]),
    )
