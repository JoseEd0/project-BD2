"""El corpus diminuto versionado en `apps/rag/tests/fixtures/datasets/` (el que siembra la
prueba de humo del compose en la CI) es el mismo que generan los tests."""

from __future__ import annotations

from pathlib import Path

from tests.rag.rag_corpus import write_corpus

from rag.settings import CORPUS_DIRECTORY

FIXTURE = Path("apps/rag/tests/fixtures/datasets") / CORPUS_DIRECTORY
VERSIONED = Path(__file__).resolve().parents[2] / FIXTURE
REGENERATE = (
    'uv run python -c "from pathlib import Path; from tests.rag.rag_corpus import '
    f"write_corpus; write_corpus(Path('{FIXTURE}'))\""
)


def test_the_versioned_tiny_corpus_is_up_to_date(tmp_path: Path):
    generated = write_corpus(tmp_path / CORPUS_DIRECTORY)
    for file in (generated.papers_file, generated.chunks_file):
        versioned = VERSIONED / file.name
        assert versioned.read_bytes() == file.read_bytes(), f"regenéralo con {REGENERATE}"
