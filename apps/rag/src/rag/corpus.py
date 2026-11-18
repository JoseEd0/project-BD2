"""Línea de comandos del corpus del RAG.

    uv run python -m rag.corpus build --data-dir data/datasets \\
        --material Proyecto_Integrador_BD2.md --material docs/informe.pdf
    uv run python -m rag.corpus figure --manifest data/datasets/rag_papers/manifest.json \\
        --output benchmarks/figures/rag-fragmentos.png

`build` cosecha la metadata de arXiv (cs.DB y cs.IR), baja los PDF con licencia que permite
redistribuirlos, extrae su texto por página y lo fragmenta, todo en
`<data-dir>/rag_papers/`. La carpeta de datos sale de `--data-dir` o de la variable
`RAG_DATA_DIR`. Volver a correr sigue desde donde quedó y no duplica nada.

`figure` dibuja el histograma de longitudes de los fragmentos a partir de un manifiesto.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import replace
from pathlib import Path

from .errors import CorpusError
from .fetch import PoliteClient
from .files import read_json
from .pipeline import CorpusPaths, build_corpus
from .settings import (
    CORPUS_DIRECTORY,
    DATA_DIR_VARIABLE,
    USER_AGENT,
    CorpusSettings,
    HarvestSettings,
)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# pypdf avisa por cada objeto raro de un PDF que igual se lee bien; solo interesa si falla.
PYPDF_LOGGER = "pypdf"


def main(arguments: list[str] | None = None) -> None:
    parser = _parser()
    options = parser.parse_args(arguments)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.getLogger(PYPDF_LOGGER).setLevel(logging.ERROR)
    try:
        options.run(options)
    except CorpusError as error:
        parser.exit(1, f"error: {error}\n")


def _parser() -> argparse.ArgumentParser:
    defaults = HarvestSettings()
    parser = argparse.ArgumentParser(prog="rag.corpus", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(required=True)
    build = commands.add_parser("build", help="construye o completa el corpus")
    build.add_argument("--data-dir", type=Path, default=_environment_data_dir())
    build.add_argument("--papers", type=int, default=defaults.paper_limit)
    build.add_argument("--categories", nargs="+", default=list(defaults.categories))
    build.add_argument("--from-date", default=defaults.from_date, help="AAAA-MM-DD")
    build.add_argument("--request-delay", type=float, default=defaults.request_delay_seconds)
    build.add_argument("--pdf-delay", type=float, default=defaults.pdf_delay_seconds)
    build.add_argument("--workers", type=int, default=CorpusSettings().extraction_workers)
    build.add_argument(
        "--material", type=Path, action="append", default=[], help="PDF o Markdown del curso"
    )
    build.add_argument(
        "--reharvest", action="store_true", help="vuelve a cosechar aunque la consulta no cambie"
    )
    build.set_defaults(run=_build)
    figure = commands.add_parser("figure", help="histograma de longitudes de los fragmentos")
    figure.add_argument("--manifest", type=Path, required=True)
    figure.add_argument("--output", type=Path, required=True)
    figure.set_defaults(run=_figure)
    return parser


def _environment_data_dir() -> Path | None:
    value = os.environ.get(DATA_DIR_VARIABLE)
    return Path(value) if value else None


def _build(options: argparse.Namespace) -> None:
    if options.data_dir is None:
        raise CorpusError(f"falta --data-dir (o la variable {DATA_DIR_VARIABLE})")
    settings = CorpusSettings(
        harvest=replace(
            HarvestSettings(),
            categories=tuple(options.categories),
            from_date=options.from_date,
            paper_limit=options.papers,
            request_delay_seconds=options.request_delay,
            pdf_delay_seconds=options.pdf_delay,
        ),
        extraction_workers=options.workers,
    )
    paths = CorpusPaths(options.data_dir / CORPUS_DIRECTORY)
    if options.reharvest:
        paths.harvest_file.unlink(missing_ok=True)
    harvest_client = PoliteClient(USER_AGENT, settings.harvest.request_delay_seconds, settings.http)
    pdf_client = PoliteClient(USER_AGENT, settings.harvest.pdf_delay_seconds, settings.http)
    manifest = build_corpus(paths, settings, options.material, harvest_client, pdf_client)
    print(f"corpus en {paths.root}: {manifest['counts']}")


def _figure(options: argparse.Namespace) -> None:
    from .figure import draw_chunk_lengths

    manifest = read_json(options.manifest)
    if not isinstance(manifest, dict):
        raise CorpusError(f"{options.manifest} no es un manifiesto del corpus")
    draw_chunk_lengths(manifest, options.output)
    print(options.output)


if __name__ == "__main__":
    main()
