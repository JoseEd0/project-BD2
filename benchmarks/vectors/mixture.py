"""Vectores sintéticos que se parecen a embeddings: una mezcla de gaussianas normalizada.

Vectores uniformes en 384 dimensiones no se parecen a los de un modelo: todos quedan casi a
la misma distancia de todos, ningún índice aproximado los separa y el recall que se mide
es el de un caso patológico. Los embeddings de texto o de imágenes, en cambio, se agrupan
por tema y, dentro de cada grupo, varían en pocas direcciones. La mezcla imita las dos
cosas:

- **`clusters` centros** al azar en la esfera unitaria, con pesos de una Dirichlet(1): hay
  temas grandes y temas chicos, como en un corpus real;
- dentro de cada grupo, el vector es el centro más una variación en un **subespacio propio
  de `intrinsic_dimension` direcciones** (de norma media `spread`) y un ruido isótropo chico
  en todas (`noise`);
- el resultado se **normaliza**, como los embeddings de E5 o CLIP que se comparan con el
  coseno.

Todo sale de una semilla: misma semilla, mismos vectores. Las consultas salen de la misma
mezcla con otra semilla, así que no están en la tabla.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from index.vector.metrics import VECTOR_DTYPE, Vectors

DIRICHLET_CONCENTRATION = 1.0
QUERY_STREAM = 1
# Filas que se generan de una vez: 4 096 vectores de 384 componentes son 6 MiB en float32.
GENERATION_BATCH = 4_096


@dataclass(frozen=True, slots=True)
class Mixture:
    """Los parámetros de la mezcla; ver el docstring del módulo."""

    dimension: int
    clusters: int
    intrinsic_dimension: int
    spread: float
    noise: float
    seed: int

    def collection(self, count: int) -> tuple[Vectors, np.ndarray]:
        """`count` vectores y el grupo de cada uno. Los primeros `n` son los mismos para
        cualquier `count >= n`: los tamaños del experimento son subconjuntos anidados."""
        return self._sample(count, np.random.default_rng(self.seed))

    def queries(self, count: int) -> Vectors:
        """`count` consultas de la misma mezcla, con otra semilla: no están en la tabla."""
        vectors, _ = self._sample(count, np.random.default_rng([self.seed, QUERY_STREAM]))
        return vectors

    def _sample(self, count: int, generator: np.random.Generator) -> tuple[Vectors, np.ndarray]:
        """Se genera siempre por lotes enteros y el último se recorta: así la fila `i` sale
        de los mismos números al azar pida quien pida cuántas."""
        centers, bases, weights = self._structure()
        vectors = np.empty((count, self.dimension), dtype=VECTOR_DTYPE)
        groups = np.empty(count, dtype=np.int64)
        for start in range(0, count, GENERATION_BATCH):
            chosen = generator.choice(self.clusters, size=GENERATION_BATCH, p=weights)
            batch = self._around(chosen, centers, bases, generator)
            kept = min(GENERATION_BATCH, count - start)
            vectors[start : start + kept] = batch[:kept]
            groups[start : start + kept] = chosen[:kept]
        return vectors, groups

    def _around(
        self,
        groups: np.ndarray,
        centers: np.ndarray,
        bases: np.ndarray,
        generator: np.random.Generator,
    ) -> Vectors:
        coordinates = generator.standard_normal((len(groups), self.intrinsic_dimension))
        variation = np.einsum("nr,nrd->nd", coordinates, bases[groups])
        isotropic = generator.standard_normal((len(groups), self.dimension))
        isotropic *= self.noise / np.sqrt(self.dimension)
        raw = centers[groups] + self.spread * variation + isotropic
        return (raw / np.linalg.norm(raw, axis=1, keepdims=True)).astype(VECTOR_DTYPE)

    def _structure(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Los centros, el subespacio de cada grupo (filas de norma `1/√r`, para que la
        variación tenga norma media 1 antes de escalarla) y el peso de cada grupo."""
        generator = np.random.default_rng([self.seed, self.clusters])
        centers = generator.standard_normal((self.clusters, self.dimension))
        centers /= np.linalg.norm(centers, axis=1, keepdims=True)
        bases = generator.standard_normal((self.clusters, self.intrinsic_dimension, self.dimension))
        bases /= np.linalg.norm(bases, axis=2, keepdims=True) * np.sqrt(self.intrinsic_dimension)
        weights = generator.dirichlet([DIRICHLET_CONCENTRATION] * self.clusters)
        return centers, bases, weights
