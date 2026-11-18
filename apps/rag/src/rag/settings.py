"""Parámetros del corpus: qué se cosecha de arXiv, a qué ritmo, cómo se limpia y cómo se
fragmenta. Cada valor tiene nombre aquí y la línea de comandos puede cambiar los que
dependen del experimento.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DATA_DIR_VARIABLE = "RAG_DATA_DIR"
CORPUS_DIRECTORY = "rag_papers"
# arXiv pide a quien automatiza que se identifique; el agente describe el programa y no
# lleva datos de ninguna persona.
USER_AGENT = "minigestor-bd2-rag/0.1 (university course project; corpus builder)"


@dataclass(frozen=True, slots=True)
class HarvestSettings:
    """Qué se pide al OAI-PMH de arXiv y cuántos papers se quedan.

    `from_date` filtra por la fecha del registro OAI (la última vez que cambió su metadata),
    no por la de envío. Los términos de uso del API piden como mínimo 3 s entre peticiones
    y una sola conexión: el corpus hace todo en serie.
    """

    endpoint: str = "https://oaipmh.arxiv.org/oai"
    metadata_prefix: str = "arXiv"
    categories: tuple[str, ...] = ("cs.DB", "cs.IR")
    from_date: str = "2024-01-01"
    paper_limit: int = 1500
    pdf_url_template: str = "https://export.arxiv.org/pdf/{arxiv_id}"
    request_delay_seconds: float = 3.0
    pdf_delay_seconds: float = 3.0


@dataclass(frozen=True, slots=True)
class HttpSettings:
    """Cómo se reintenta una petición que falla por la red o porque el servidor está ocupado."""

    timeout_seconds: float = 120.0
    attempts: int = 5
    # Espera ante un 503 sin `Retry-After`, y tope de la que se acepta de un servidor: un
    # `Retry-After` de horas dejaría el proceso dormido sin decir nada.
    default_retry_after_seconds: float = 30.0
    max_retry_after_seconds: float = 600.0


@dataclass(frozen=True, slots=True)
class CleaningSettings:
    """Cómo se reconocen los encabezados y pies de página que se repiten.

    Solo se miran las `edge_lines` primeras y últimas líneas de cada página. Una de ellas es
    encabezado o pie si aparece, con los dígitos igualados, en al menos `repeated_page_share`
    de las páginas y nunca en menos de `min_repeated_pages`: el título de una sección que
    abre dos páginas no es un encabezado.
    """

    edge_lines: int = 3
    repeated_page_share: float = 0.5
    min_repeated_pages: int = 3
    # Un PDF escaneado sin capa de texto suele dar unas pocas palabras sueltas (el sello de
    # arXiv del margen, un número de página); uno con texto da cientos por página.
    min_words_per_page: float = 25.0


@dataclass(frozen=True, slots=True)
class ChunkSettings:
    """Tamaño y solapamiento de los fragmentos, en palabras.

    Los fragmentos crecen hasta `target_words` y nunca pasan de `max_words`. El objetivo
    sale del modelo de embeddings, multilingual-e5-small, que trunca en 512 tokens: su
    tokenizador parte el texto de estos papers (fórmulas, tablas, siglas) en unos dos tokens
    por palabra, y con 220 palabras cuatro de cada cinco fragmentos entran enteros (con 300,
    solo dos de cada cinco; ver el README del servicio). El solapamiento son las últimas
    oraciones del fragmento anterior, entre `overlap_words` y `max_overlap_words`.
    """

    min_words: int = 200
    target_words: int = 220
    max_words: int = 500
    overlap_words: int = 50
    max_overlap_words: int = 120

    @property
    def long_sentence_words(self) -> int:
        """Una «oración» más larga que esto (una tabla o una lista de referencias sin
        puntos) se parte por palabras. Con este tope, un fragmento con menos de
        `min_words` siempre admite la oración siguiente sin pasar de `max_words`."""
        return self.max_words - self.min_words


@dataclass(frozen=True, slots=True)
class CorpusSettings:
    """Todo lo que decide qué corpus sale de una corrida."""

    harvest: HarvestSettings = field(default_factory=HarvestSettings)
    http: HttpSettings = field(default_factory=HttpSettings)
    cleaning: CleaningSettings = field(default_factory=CleaningSettings)
    chunks: ChunkSettings = field(default_factory=ChunkSettings)
    extraction_workers: int = field(default_factory=lambda: os.cpu_count() or 1)
