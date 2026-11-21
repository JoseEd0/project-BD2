"""Figuras comparativas a partir de los resultados crudos de los benchmarks.

    uv sync --all-packages --extra plots
    .venv/bin/python -m benchmarks.plot

Lee los JSON de `results/` y escribe en `figures/` una imagen por comparación del
enunciado. Cada imagen es una fila de paneles pequeños, uno por operación, con el tamaño
del conjunto de datos en el eje horizontal y una línea por técnica.

Cada panel se tiene que entender solo: su título dice la operación, el eje vertical dice
qué se mide y en qué unidad, y el valor que se rotula al final de cada línea lleva esa
misma unidad.

Los dos ejes son logarítmicos: los tamaños van de 1 000 a 100 000 y los tiempos cubren
cuatro órdenes de magnitud, así que en escala lineal solo se vería la técnica más lenta.
En un eje logarítmico una recta es una ley de potencia y su pendiente dice cómo escala la
técnica: plana si no depende del tamaño, a 45° si crece en proporción.

Las medidas que son fracciones de 0 a 1, como la precisión, van en barras agrupadas sobre
un eje lineal, con un panel por medida y una fila por tamaño.

Qué se dibuja está declarado en `benchmarks/plots/`, un módulo por experimento.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from benchmarks.common import RESULTS_DIRECTORY
from benchmarks.plots import FIGURES
from benchmarks.plots.drawing import draw, load

FIGURES_DIRECTORY = "figures"
REPORT_FIGURES_DIRECTORY = Path("docs") / "latex" / "figuras"
# Las figuras que incluye el informe. Ahí van sin título ni subtítulo, que repetirían el pie
# de LaTeX, para que a todo el ancho de la página sus rótulos se lean.
REPORT_FIGURES = frozenset(
    {
        "almacenamiento",
        "indices",
        "espacial-radio",
        "espacial-knn",
        "espacial-indice",
        "texto-consultas",
        "multimedia-consulta",
        "rostros-tiempo-real",
        "carga-replicas",
    }
)


def main() -> None:
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=here / RESULTS_DIRECTORY)
    parser.add_argument("--output", type=Path, default=here / FIGURES_DIRECTORY)
    parser.add_argument(
        "--report-output", type=Path, default=here.parent / REPORT_FIGURES_DIRECTORY
    )
    arguments = parser.parse_args()
    for figure in FIGURES:
        source = arguments.results / f"{figure.report}.json"
        if not source.exists():
            print(f"falta {source}: no se dibuja {figure.filename}")
            continue
        report = load(source)
        print(draw(figure, report, arguments.output))
        if figure.filename in REPORT_FIGURES:
            untitled = replace(figure, title="", subtitle="")
            print(draw(untitled, report, arguments.report_output))


if __name__ == "__main__":
    main()
