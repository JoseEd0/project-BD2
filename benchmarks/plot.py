"""Figuras comparativas a partir de los resultados crudos de los benchmarks.

    .venv/bin/pip install -e ".[plots]"
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

Qué se dibuja está declarado en `benchmarks/plots/`, un módulo por experimento.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.common import RESULTS_DIRECTORY
from benchmarks.plots import FIGURES
from benchmarks.plots.drawing import draw, load

FIGURES_DIRECTORY = "figures"


def main() -> None:
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=here / RESULTS_DIRECTORY)
    parser.add_argument("--output", type=Path, default=here / FIGURES_DIRECTORY)
    arguments = parser.parse_args()
    for figure in FIGURES:
        source = arguments.results / f"{figure.report}.json"
        if not source.exists():
            print(f"falta {source}: no se dibuja {figure.filename}")
            continue
        print(draw(figure, load(source), arguments.output))


if __name__ == "__main__":
    main()
