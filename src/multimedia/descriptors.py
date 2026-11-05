"""La forma común de los descriptores locales de cualquier archivo multimedia."""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
import numpy.typing as npt

# Una fila por descriptor local (un punto clave SIFT o una ventana de audio) y una columna
# por dimensión, en `float32`: la precisión con la que la cuantización los compara.
Descriptors: TypeAlias = npt.NDArray[np.float32]
DESCRIPTOR_DTYPE = np.float32


def no_descriptors(dimension: int) -> Descriptors:
    """La matriz de un archivo del que no sale ningún descriptor: `(0, dimension)`."""
    return np.empty((0, dimension), dtype=DESCRIPTOR_DTYPE)
