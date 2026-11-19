"""Nombres de la evaluación del RAG: los usa el experimento al guardar sus medidas y
`benchmarks.plots.rag_evaluation` al dibujarlas."""

RETRIEVAL_REPORT = "rag_evaluacion"
ANSWERS_REPORT = "rag_evaluacion_respuestas"
PER_QUESTION_OPERATION = "pregunta"
ANSWER_OPERATION = "respuesta"

ALL_GROUP = "todas"
DB_GROUP = "cs.DB"
IR_GROUP = "cs.IR"
COURSE_GROUP = "curso (es)"
AUTHOR_GROUP = "escritas a mano"
BATCH_GROUP = "generadas (Batch API)"

RECALL_FIELD = "recall_{k}"
MRR_FIELD = "mrr"
NEIGHBOUR_SUFFIX = "_vecino"
PAPER_SUFFIX = "_paper"
