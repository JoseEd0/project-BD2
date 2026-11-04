"""Velocidad del analizador de texto: tokens por segundo sobre texto real en español.

    .venv/bin/python -m benchmarks.text_analyzer_benchmark --repetitions 5 \\
        --input Proyecto_Integrador_BD2.md README.md src/*/README.md benchmarks/README.md

Cada línea no vacía de los archivos es un documento, y se analiza como lo hará SPIMI: con
`Analyzer.analyze`, que da las frecuencias y la longitud. La primera pasada usa un
analizador recién creado, con la caché de raíces vacía; las siguientes lo reutilizan, como
ocurre al indexar un corpus grande, donde casi todas las palabras ya se vieron. Se cuentan
los tokens que corta el patrón, antes de quitar stopwords.
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Sequence
from pathlib import Path

from benchmarks.common import Report, results_directory, time_it
from config import EngineConfig
from query.plan import MILLISECONDS_PER_SECOND
from text import DEFAULT_LANGUAGE, TOKEN_PATTERN, Analyzer, fold

REPORT_NAME = "analizador_texto"
TECHNIQUE = "analizador"
COLD_PASS = "pasada fría"
WARM_PASS = "pasada caliente (mediana)"
DEFAULT_REPETITIONS = 5
INPUT_ENCODING = "utf-8"


def read_documents(paths: Sequence[Path]) -> list[str]:
    """Una línea no vacía de cada archivo es un documento."""
    return [
        line
        for path in paths
        for line in path.read_text(encoding=INPUT_ENCODING).splitlines()
        if line.strip()
    ]


def run(documents: Sequence[str], repetitions: int, language: str) -> Report:
    """Una pasada fría y `repetitions` pasadas calientes sobre los documentos."""
    tokens = sum(len(TOKEN_PATTERN.findall(fold(document))) for document in documents)
    analyzer = Analyzer(EngineConfig(), language)
    terms = sum(analyzer.analyze(document).length for document in documents)
    report = Report(
        REPORT_NAME,
        {"documentos": len(documents), "tokens": tokens, "términos": terms, "idioma": language},
    )
    cold = _analyze_all(Analyzer(EngineConfig(), language), documents)
    _add_measurement(report, COLD_PASS, len(documents), cold, tokens)
    warm = [_analyze_all(analyzer, documents) for _ in range(repetitions)]
    _add_measurement(report, WARM_PASS, len(documents), statistics.median(warm), tokens)
    return report


def _add_measurement(
    report: Report, operation: str, documents: int, milliseconds: float, tokens: int
) -> None:
    tokens_per_second = round(tokens * MILLISECONDS_PER_SECOND / milliseconds)
    report.add(TECHNIQUE, operation, documents, milliseconds, tokens_por_segundo=tokens_per_second)


def _analyze_all(analyzer: Analyzer, documents: Sequence[str]) -> float:
    return time_it(lambda: [analyzer.analyze(document) for document in documents])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    report = run(read_documents(arguments.input), arguments.repetitions, arguments.language)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
