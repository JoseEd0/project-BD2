"""Genera `e5_reference.json`: tokens y embeddings de multilingual-e5-small calculados con la
implementación de referencia (transformers y PyTorch, la receta de la tarjeta del modelo).

No corre en los tests ni en la CI: PyTorch no es dependencia del proyecto. Se corre una vez,
en un entorno aparte, con la misma revisión del modelo que fija `rag.models`:

    uv venv /tmp/e5-ref && uv pip install --python /tmp/e5-ref/bin/python torch transformers
    /tmp/e5-ref/bin/python apps/rag/tests/fixtures/e5_reference.py \\
        apps/rag/tests/fixtures/e5_reference.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
import torch.nn.functional as functional
import transformers
from transformers import AutoModel, AutoTokenizer

REPOSITORY = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MAX_TOKENS = 512
DECIMALS = 7
LONG_REPETITIONS = 200

TEXTS = (
    "query: ¿Qué índice usa una base de datos para buscar vecinos más cercanos?",
    "query: how does BM25 normalize term frequency by document length?",
    "passage: Exact k-nearest-neighbor (k-NN) vector search is a common feature of vector "
    "libraries and database systems. It serves applications that require exact results and "
    "provides the ground truth for evaluating approximate search.",
    "passage: El árbol B+ guarda las filas en las hojas y las enlaza para recorrer rangos.",
    "passage: " + " ".join(["The buffer pool caches disk pages in memory."] * LONG_REPETITIONS),
)


def main(output: Path) -> None:
    tokenizer = AutoTokenizer.from_pretrained(REPOSITORY, revision=REVISION)
    model = AutoModel.from_pretrained(REPOSITORY, revision=REVISION).eval()
    batch = tokenizer(
        list(TEXTS), max_length=MAX_TOKENS, padding=True, truncation=True, return_tensors="pt"
    )
    with torch.no_grad():
        hidden = model(**batch).last_hidden_state
    mask = batch["attention_mask"]
    pooled = (hidden * mask[..., None]).sum(dim=1) / mask.sum(dim=1)[..., None]
    vectors = functional.normalize(pooled, p=2, dim=1)
    items = [
        {
            "text": text,
            "input_ids": batch["input_ids"][row][mask[row].bool()].tolist(),
            "vector": [round(value, DECIMALS) for value in vectors[row].tolist()],
        }
        for row, text in enumerate(TEXTS)
    ]
    reference = {
        "model": REPOSITORY,
        "revision": REVISION,
        "produced_with": f"transformers {transformers.__version__}, torch {torch.__version__}",
        "items": items,
    }
    output.write_text(json.dumps(reference, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
