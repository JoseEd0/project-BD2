"""Gráficas comparativas a partir de los resultados crudos de los benchmarks.

Requiere matplotlib:

    .venv/bin/pip install -e ".[plots]"
    .venv/bin/python -m benchmarks.plot --report benchmarks/results/indices.json

Genera un PNG por operación con el tiempo frente al tamaño del conjunto de datos.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

FIGURE_WIDTH = 7.0
FIGURE_HEIGHT = 4.0
MARKER = "o"
SKIPPED_OPERATIONS = frozenset({"espacio en disco", "espacio"})


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def series_by_operation(report: dict[str, Any]) -> dict[str, dict[str, list[tuple[int, float]]]]:
    """Agrupa las medidas por operación y técnica."""
    grouped: dict[str, dict[str, list[tuple[int, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in report["measurements"]:
        if item["operation"] in SKIPPED_OPERATIONS or item["milliseconds"] == 0.0:
            continue
        grouped[item["operation"]][item["technique"]].append(
            (item["dataset_size"], item["milliseconds"])
        )
    return grouped


def draw(report: dict[str, Any], output: Path) -> list[Path]:
    """Escribe un PNG por operación y devuelve las rutas creadas."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for operation, techniques in series_by_operation(report).items():
        figure, axes = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_HEIGHT))
        for technique, points in sorted(techniques.items()):
            points.sort()
            axes.plot(
                [size for size, _ in points],
                [milliseconds for _, milliseconds in points],
                marker=MARKER,
                label=technique,
            )
        axes.set_title(f"{report['name']} — {operation}")
        axes.set_xlabel("filas")
        axes.set_ylabel("milisegundos")
        axes.legend()
        axes.grid(visible=True, alpha=0.3)
        path = output / f"{report['name']}-{operation.replace(' ', '-')}.png"
        figure.tight_layout()
        figure.savefig(path, dpi=150)
        plt.close(figure)
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()
    report = load(arguments.report)
    destination = arguments.output or arguments.report.parent / "graficas"
    for path in draw(report, destination):
        print(path)


if __name__ == "__main__":
    main()
