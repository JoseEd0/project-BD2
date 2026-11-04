"""Saca del corpus ESCI la muestra de productos en español que usa la demostración de texto.

Son unos 2 000 productos reales de Amazon España: todos los que los anotadores juzgaron
para un puñado de consultas elegidas a mano —auriculares, zapatillas, mochilas, cafeteras,
portátiles, fundas, cargadores y cocina—, y el resto, productos del catálogo tomados al azar
con la semilla. Así cada consulta de la demo tiene decenas de candidatos que se parecen
entre sí, que es donde TF-IDF y BM25 dejan de coincidir, y el relleno hace de ruido.

Necesita el corpus que descarga `benchmarks.esci.download` y se ejecuta desde la raíz del
repositorio:

    .venv/bin/python -m demos.muestra_esci --corpus data/datasets/esci_es \\
        --salida demos/samples/esci

Escribe los tres CSV del corpus (`productos.csv`, `consultas.csv`, `juicios.csv`) con su
mismo formato. Misma semilla, misma muestra, byte a byte.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.esci.subsets import (
    DEFAULT_SEED,
    load_corpus,
    queries_named,
    subset_with_queries,
    write_as_corpus,
)

DEFAULT_PRODUCTS = 2000

# Varias consultas por tema, para que se pisen el vocabulario: «zapatillas running mujer»
# encuentra también las zapatillas de casa y las de ciclismo, y el orden es lo que decide.
# Las que juzgan sustitutos o irrelevantes además de exactos sirven para ver, con un JOIN a
# `juicios`, si el ranking pone arriba lo que un anotador calificó como exacto.
DEMO_QUERIES = (
    "auriculares inalambricos",
    "auriculares bluetooth inalámbricos",
    "auriculares cancelación de ruido",
    "auriculares gaming",
    "auriculares sin cable",
    "auriculares deportivos",
    "auriculares de diadema",
    "zapatillas running hombre",
    "zapatillas running mujeres",
    "puma zapatillas mujer running",
    "zapatillas mujer",
    "zapatillas de mujer sin cordones",
    "zapatillas de casa para hombre",
    "zapatillas ciclismo carretera",
    "mochila portatil 15 6 pulgadas mujer",
    "mochila para moto impermeable",
    "mochila escolar niña",
    "mochilas de montaña",
    "mochila antirrobo",
    "cafetera italiana induccion",
    "cafetera capsula",
    "cafetera superautomatica delonghi",
    "cafetera americana induccion",
    "cafetera portatil",
    "asus gaming portatil",
    "dell portatiles",
    "hp portatiles i5",
    "funda para portatil hp",
    "funda iphone 11 pro max",
    "funda iphone xr libro",
    "cargador iphone",
    "cargador samsung original",
    "cargador carga ultra rapida samsung",
    "aceite de oliva virgen extra sin filtrar",
    "accesorios freidora sin aceite princess",
    "cecotec freidora sin aceite",
)


def write_sample(corpus_directory: Path, destination: Path, products: int, seed: int) -> None:
    """Escribe en `destination` la muestra de `products` productos con las consultas de la
    demo y todos sus juicios.

    Raises:
        SubsetError: si el corpus no tiene alguna de las consultas o sus productos juzgados
            no caben en `products`.
    """
    corpus = load_corpus(corpus_directory)
    sample = subset_with_queries(corpus, products, queries_named(corpus, DEMO_QUERIES), seed)
    write_as_corpus(sample, corpus_directory, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True, help="carpeta del corpus ESCI")
    parser.add_argument("--salida", type=Path, required=True, help="carpeta de la muestra")
    parser.add_argument("--productos", type=int, default=DEFAULT_PRODUCTS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    arguments = parser.parse_args()
    write_sample(arguments.corpus, arguments.salida, arguments.productos, arguments.seed)
    print(f"Muestra de {arguments.productos} productos en {arguments.salida}")


if __name__ == "__main__":
    main()
