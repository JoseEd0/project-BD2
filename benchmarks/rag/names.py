"""Nombres de técnicas y etapas del experimento de la ingesta del RAG: los usa el
experimento al guardar sus medidas y `benchmarks.plots.rag` al dibujarlas."""

REPORT = "rag_ingesta"
EMBEDDING_OPERATION = "embeddings de fragmentos"
STAGES_OPERATION = "etapas de la ingesta"
THROUGHPUT_FIELD = "fragmentos_por_segundo"
LOAD_ONLY = "carga NDJSON sin índices"
INVERTED_BUILD = "CREATE INDEX invertido (SPIMI)"
HNSW_BUILD = "CREATE INDEX HNSW (FAISS)"
DEFERRED_LOAD = "carga con índices diferidos (la de la siembra)"
