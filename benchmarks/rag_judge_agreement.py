"""El acuerdo entre el juez de fidelidad y una revisión a mano (#87).

    # qué respuestas revisar: una muestra con semilla de las que el juez calificó
    uv run --no-sync python -m benchmarks.rag_judge_agreement sample --size 20

    # después de escribir benchmarks/rag/revision_fidelidad.jsonl a mano
    uv run --no-sync python -m benchmarks.rag_judge_agreement score

La muestra sale de `rag_evaluacion_respuestas.json` (el experimento con `--judge`): las
respuestas con veredicto, ordenadas por pregunta y barajadas con `--seed`. Quien revisa lee
cada respuesta junto a sus fragmentos (`fragmentos_enviados`) y escribe una línea por
pregunta, `{"pregunta_id": "a07", "fiel": true}`, con `fiel` falso si alguna afirmación no
sale de los fragmentos. `score` compara esas etiquetas con las del juez y guarda la
fracción de acuerdo y la kappa de Cohen en `rag_evaluacion_acuerdo.json`.
"""

from __future__ import annotations

import argparse
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmarks.common import results_directory
from benchmarks.rag.evaluation_names import ANSWERS_REPORT
from rag.evaluation.metrics import agreement
from rag.files import read_json, read_jsonl, write_json

HERE = Path(__file__).parent
DEFAULT_LABELS = HERE / "rag" / "revision_fidelidad.jsonl"
AGREEMENT_REPORT = "rag_evaluacion_acuerdo.json"
DEFAULT_SAMPLE_SIZE = 20
DEFAULT_SEED = 87


def judged_answers(report: Mapping[str, Any]) -> dict[str, bool]:
    """El veredicto del juez (`fiel`) por pregunta, solo de las respuestas que tienen uno."""
    verdicts: dict[str, bool] = {}
    for measurement in report["measurements"]:
        extra = measurement["extra"]
        judge = extra.get("juez") or {}
        if isinstance(judge.get("fiel"), bool):
            verdicts[extra["pregunta_id"]] = judge["fiel"]
    return verdicts


def review_sample(verdicts: Mapping[str, bool], size: int, seed: int) -> list[str]:
    """Hasta `size` preguntas juzgadas, elegidas con `seed`, en orden de pregunta."""
    chosen = random.Random(seed).sample(sorted(verdicts), min(size, len(verdicts)))
    return sorted(chosen)


def score(verdicts: Mapping[str, bool], labels: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """El acuerdo entre el juez y las etiquetas a mano de las preguntas que tienen las dos.

    Raises:
        ValueError: si ninguna etiqueta corresponde a una respuesta juzgada.
    """
    paired = [(verdicts[row["pregunta_id"]], bool(row["fiel"])) for row in labels
              if row["pregunta_id"] in verdicts]  # fmt: skip
    result = agreement([judge for judge, _ in paired], [human for _, human in paired])
    return {
        "casos": result.cases,
        "acuerdo": round(result.accuracy, 4),
        "kappa_de_cohen": round(result.cohen_kappa, 4),
        "fieles_segun_el_juez": sum(judge for judge, _ in paired),
        "fieles_segun_la_revision": sum(human for _, human in paired),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["sample", "score"])
    parser.add_argument(
        "--report", type=Path, default=results_directory(HERE) / f"{ANSWERS_REPORT}.json"
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=results_directory(HERE) / AGREEMENT_REPORT)
    arguments = parser.parse_args()
    report = read_json(arguments.report)
    if not isinstance(report, dict):
        raise SystemExit(f"{arguments.report} no es un informe de respuestas")
    verdicts = judged_answers(report)
    if arguments.command == "sample":
        print("\n".join(review_sample(verdicts, arguments.size, arguments.seed)))
        return
    result = score(verdicts, list(read_jsonl(arguments.labels)))
    write_json(arguments.output, result)
    print(result)


if __name__ == "__main__":
    main()
