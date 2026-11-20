"""El corpus diminuto de los tests (`tests/fixtures/datasets/rag_papers`) con sus PDF, para
abrir una cita en el navegador contra el servicio real. El fixture solo trae los JSONL: aquí
cada documento recibe un PDF de tantas páginas como declara, con el texto de cada fragmento
en su primera página, que es donde la interfaz busca el pasaje. Solo usa la biblioteca
estándar; lo usa la suite e2e (`e2e/run.sh`):

    python3 -m apps.rag.tests.tiny_corpus <carpeta de datasets>
"""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

from apps.rag.tests.rag_pdf_factory import pdf_bytes

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "datasets" / "rag_papers"
CORPUS_DIRECTORY = "rag_papers"
CORPUS_FILES = ("papers.jsonl", "fragmentos.jsonl")
# Helvetica de 11 puntos en los 468 de ancho útil de una carta: unos 85 caracteres.
LINE_CHARACTERS = 80


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _page_lines(paper: dict[str, object], chunks: list[dict[str, object]]) -> list[list[str]]:
    pages: list[list[str]] = [[] for _ in range(int(str(paper["pages"])))]
    for chunk in chunks:
        first_page = int(str(chunk["page_start"]))
        pages[first_page - 1] += textwrap.wrap(str(chunk["text"]), LINE_CHARACTERS)
    return pages


def write_tiny_corpus(datasets_dir: Path) -> Path:
    """Copia el corpus del fixture a `<datasets_dir>/rag_papers` y escribe el PDF de cada
    documento en su `file_path`. Devuelve la carpeta del corpus."""
    corpus = datasets_dir / CORPUS_DIRECTORY
    corpus.mkdir(parents=True, exist_ok=True)
    for name in CORPUS_FILES:
        shutil.copyfile(FIXTURE_CORPUS / name, corpus / name)
    chunks_by_paper: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for chunk in _read_jsonl(FIXTURE_CORPUS / "fragmentos.jsonl"):
        chunks_by_paper[str(chunk["paper_id"])].append(chunk)
    for paper in _read_jsonl(FIXTURE_CORPUS / "papers.jsonl"):
        pdf = corpus / str(paper["file_path"])
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(pdf_bytes(_page_lines(paper, chunks_by_paper[str(paper["paper_id"])])))
    return corpus


if __name__ == "__main__":
    print(write_tiny_corpus(Path(sys.argv[1])))
