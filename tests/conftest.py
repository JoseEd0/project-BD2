"""Fixtures compartidas: siempre un directorio de datos temporal y páginas pequeñas.

Las páginas chicas son deliberadas: obligan a que los tests crucen fronteras de página,
que es donde viven los errores de las estructuras de disco.
"""

from pathlib import Path

import pytest

from config import EngineConfig

TEST_PAGE_SIZE = 256
TEST_BUFFER_POOL_PAGES = 4


@pytest.fixture
def config(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        page_size=TEST_PAGE_SIZE,
        buffer_pool_pages=TEST_BUFFER_POOL_PAGES,
        data_directory=tmp_path,
    )
