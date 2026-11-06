"""Los nombres con que el experimento multimedia anota sus medidas y las figuras las buscan.

Viven en un solo lugar para que una figura no deje de encontrar una serie porque alguien
cambió un acento en el experimento.
"""

from __future__ import annotations

from index.vector import VectorMetric

IMAGES = "imágenes"
AUDIO = "audio"

EXACT = "búsqueda exacta (gestor)"
ENGINE_IVF = "IVF (gestor)"
ENGINE_HNSW = "HNSW (gestor)"
FAISS_IVF = "IVF (FAISS directo)"
FAISS_HNSW = "HNSW (FAISS directo)"
FAISS_HNSW_WITHOUT_ZEROS = "HNSW (FAISS, sin vectores cero)"
USEARCH_HNSW = "HNSW (USearch)"
PGVECTOR_IVF = "ivfflat (pgvector)"
PGVECTOR_HNSW = "hnsw (pgvector)"
PGVECTOR_TABLE = "tabla (pgvector)"
SQL_IVF = "IVF (gestor, SQL)"
SQL_HNSW = "HNSW (gestor, SQL)"
RANDOM_NEIGHBOURS = "vecinos al azar"
RAW_VECTORS = "vectores en bruto"
FEATURE_STORE = "almacén de características"

SAMPLE_STAGE = "muestra del vocabulario"
VOCABULARY_STAGE = "vocabulario (k-means)"
EXTRACTION_STAGE = "extracción"
QUANTIZATION_STAGE = "cuantización y TF-IDF"
READING_STAGE = "lectura de los vectores"
QUERY_EXTRACTION_STAGE = "extracción de la consulta"
QUERY_VECTOR_STAGE = "vector de la consulta"

METRIC_LABELS = {
    VectorMetric.EUCLIDEAN: "euclidiana",
    VectorMetric.COSINE: "coseno",
    VectorMetric.DOT: "producto punto",
}

FEATURES = "características"
BUILD = "construcción"
MEMORY = "memoria"
QUERY = "consulta"
CURVE = "curva"
CREATE_INDEX = "CREATE INDEX"
GALLERY = "galería"


def dataset_operation(dataset: str, what: str) -> str:
    """La operación de algo que no depende de la métrica: `imágenes: características`."""
    return f"{dataset}: {what}"


def metric_operation(dataset: str, metric: VectorMetric, what: str) -> str:
    """La operación de una medida con una métrica: `imágenes · coseno: consulta`."""
    return f"{dataset} · {METRIC_LABELS[metric]}: {what}"
