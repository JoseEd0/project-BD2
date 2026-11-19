"""La evaluación del RAG (#87): el conjunto de preguntas, las métricas de recuperación, la
generación de preguntas con la Batch API y el juez de fidelidad de las respuestas.

- `dataset`: las preguntas en español, cada una con el fragmento que la responde (o sin él,
  si el corpus no tiene la respuesta), y la muestra estratificada de fragmentos de la que
  salen.
- `metrics`: Recall@k, MRR y el acuerdo entre el juez y una revisión a mano.
- `generation`: las peticiones de la Batch API que escriben preguntas a partir de un
  fragmento, el filtro de lo que vuelve y la lista para revisarlo a mano.
- `judge`: el juez que decide, afirmación por afirmación, si una respuesta se apoya en los
  fragmentos que se le dieron al modelo.
"""
