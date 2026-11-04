"""Las figuras de los experimentos: un módulo por experimento, con su `FIGURES`.

`model` declara qué puede llevar una figura, `style` su aspecto y `drawing` cómo se pinta;
un experimento nuevo solo añade su módulo y lo suma aquí.
"""

from benchmarks.plots import indexes, spatial, storage, text_search
from benchmarks.plots.model import BarFigure, Figure

FIGURES: tuple[Figure | BarFigure, ...] = (
    *storage.FIGURES,
    *indexes.FIGURES,
    *spatial.FIGURES,
    *text_search.FIGURES,
)
