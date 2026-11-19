"""Las figuras de los experimentos: un módulo por experimento, con su `FIGURES`.

`model` declara qué puede llevar una figura, `style` su aspecto y `drawing` cómo se pinta;
un experimento nuevo solo añade su módulo y lo suma aquí.
"""

from benchmarks.plots import (
    copyright_catalog,
    copyright_detection,
    faces,
    faces_evaluation,
    faces_service,
    faces_stream,
    indexes,
    ingest,
    multimedia,
    rag,
    rag_evaluation,
    segments,
    shop,
    shop_recommend,
    spatial,
    storage,
    text_search,
    vectors,
)
from benchmarks.plots.model import (
    BarFigure,
    BreakdownFigure,
    CurveFigure,
    Figure,
    GalleryFigure,
    XYFigure,
)

FIGURES: tuple[
    Figure | BarFigure | CurveFigure | GalleryFigure | BreakdownFigure | XYFigure, ...
] = (
    *storage.FIGURES,
    *indexes.FIGURES,
    *spatial.FIGURES,
    *text_search.FIGURES,
    *multimedia.FIGURES,
    *vectors.FIGURES,
    *faces.FIGURES,
    *faces_service.FIGURES,
    *faces_stream.FIGURES,
    *faces_evaluation.FIGURES,
    *segments.FIGURES,
    *ingest.FIGURES,
    *shop.FIGURES,
    *copyright_catalog.FIGURES,
    *copyright_detection.FIGURES,
    *rag.FIGURES,
    *shop_recommend.FIGURES,
    *rag_evaluation.FIGURES,
)
