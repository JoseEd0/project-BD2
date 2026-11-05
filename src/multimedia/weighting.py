"""TF-IDF sobre el histograma de palabras de un objeto.

Es el peso de Sivic y Zisserman («Video Google», ICCV 2003), el mismo de la recuperación
de texto aplicado a palabras visuales:

    t_i = (n_id / n_d) · log(N / n_i)

`n_id` es cuántos descriptores del objeto caen en la palabra i, `n_d` cuántos descriptores
tiene el objeto, `N` cuántos objetos hay y `n_i` en cuántos aparece la palabra. La
frecuencia relativa quita el efecto de que una imagen grande o un audio largo den más
descriptores; el idf apaga las palabras que salen en casi todo (un cielo, un silencio).

El idf se fija al construir. Un objeto que llega después se pesa con el idf de entonces, y
reconstruir lo recalcula.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
import numpy.typing as npt

Weights: TypeAlias = npt.NDArray[np.float32]
InverseFrequencies: TypeAlias = npt.NDArray[np.float64]


def inverse_document_frequencies(
    document_frequencies: npt.ArrayLike, documents: int
) -> InverseFrequencies:
    """`log(N / n_i)` por palabra; 0 para una palabra que no aparece en ningún objeto."""
    frequencies = np.asarray(document_frequencies, dtype=np.float64)
    idf = np.zeros_like(frequencies)
    present = frequencies > 0
    idf[present] = np.log(documents / frequencies[present])
    return idf


def tf_idf(word_counts: npt.ArrayLike, idf: InverseFrequencies) -> Weights:
    """El vector TF-IDF de uno o varios histogramas (una fila por objeto). Un objeto sin
    descriptores da el vector cero."""
    counts = np.asarray(word_counts, dtype=np.float64)
    totals = counts.sum(axis=-1, keepdims=True)
    frequencies = np.divide(counts, totals, out=np.zeros_like(counts), where=totals > 0)
    return (frequencies * idf).astype(np.float32)
