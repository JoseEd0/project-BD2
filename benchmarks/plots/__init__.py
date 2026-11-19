"""Las figuras de los experimentos: un módulo por experimento, con su `FIGURES`.

`model` declara qué puede llevar una figura, `style` su aspecto y `drawing` cómo se pinta;
un experimento nuevo solo añade su módulo y lo suma aquí.
"""

from benchmarks.plots import (
    copyright_catalog,
    faces,
    faces_service,
    indexes,
    ingest,
    multimedia,
    rag,
    segments,
    shop,
    spatial,
    storage,
    text_search,
    vectors,
)
from benchmarks.plots.model import BarFigure, BreakdownFigure, CurveFigure, Figure, GalleryFigure

FIGURES: tuple[Figure | BarFigure | CurveFigure | GalleryFigure | BreakdownFigure, ...] = (
    *storage.FIGURES,
    *indexes.FIGURES,
    *spatial.FIGURES,
    *text_search.FIGURES,
    *multimedia.FIGURES,
    *vectors.FIGURES,
    *faces.FIGURES,
    *faces_service.FIGURES,
    *segments.FIGURES,
    *ingest.FIGURES,
    *shop.FIGURES,
    *copyright_catalog.FIGURES,
    *rag.FIGURES,
)
