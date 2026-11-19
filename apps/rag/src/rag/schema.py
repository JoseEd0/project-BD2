"""Las tablas del servicio en el gestor y sus migraciones repetibles.

`rag_papers` guarda la metadata de cada documento; `rag_chunks`, cada fragmento con su
página, su texto (con un índice invertido en inglés para BM25) y su embedding (con un HNSW
por coseno). Los índices se crean con las tablas vacías: así la primera carga masiva los
difiere y los construye de una vez al terminar (SPIMI y FAISS), en vez de fila a fila.
"""

from __future__ import annotations

from .embeddings import EMBEDDING_DIMENSIONS

PAPERS_TABLE = "rag_papers"
CHUNKS_TABLE = "rag_chunks"
CONTENT_INDEX = "rag_chunks_content"
EMBEDDING_INDEX = "rag_chunks_embedding"

# El identificador más largo del corpus tiene 29 caracteres (`curso:proyecto-integrador-bd2`)
# y el de un fragmento, 32; los arXiv nuevos tienen 10. El margen admite otros materiales.
ID_LENGTH = 64
SHORT_TEXT_LENGTH = 32
URL_LENGTH = 200
SHA256_LENGTH = 64
INDEX_LANGUAGE = "en"
# M = 16 es el valor de pgvector y de FAISS. ef_construction = 128, el doble del valor por
# omisión del gestor: el grafo se construye una sola vez y la recuperación del RAG vale más
# que unos segundos de carga.
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 128

SCHEMA: tuple[str, ...] = (
    f"""CREATE TABLE IF NOT EXISTS {PAPERS_TABLE} (
        paper_id VARCHAR({ID_LENGTH}) PRIMARY KEY,
        source VARCHAR({SHORT_TEXT_LENGTH}),
        title TEXT,
        authors TEXT,
        year INT,
        abstract TEXT,
        primary_category VARCHAR({SHORT_TEXT_LENGTH}),
        license VARCHAR({URL_LENGTH}),
        url VARCHAR({URL_LENGTH}),
        file_path VARCHAR({URL_LENGTH}),
        media_type VARCHAR({SHORT_TEXT_LENGTH}),
        sha256 VARCHAR({SHA256_LENGTH}),
        pages INT
    )""",
    f"""CREATE TABLE IF NOT EXISTS {CHUNKS_TABLE} (
        chunk_id VARCHAR({ID_LENGTH}) PRIMARY KEY,
        paper_id VARCHAR({ID_LENGTH}),
        ordinal INT,
        page_start INT,
        page_end INT,
        words INT,
        content TEXT,
        embedding VECTOR({EMBEDDING_DIMENSIONS})
    )""",
    f"CREATE INDEX IF NOT EXISTS {CONTENT_INDEX} ON {CHUNKS_TABLE} USING INVERTED (content) "
    f"WITH (idioma = '{INDEX_LANGUAGE}')",
    f"CREATE INDEX IF NOT EXISTS {EMBEDDING_INDEX} ON {CHUNKS_TABLE} USING HNSW (embedding) "
    f"WITH (metric = 'cosine', m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})",
)

AUTHOR_SEPARATOR = "; "
