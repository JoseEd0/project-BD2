"""El tokenizador BPE de CLIP sin PyTorch: la librería `tokenizers` con el `tokenizer.json`
que publica el modelo.

Cada texto empieza con `<|startoftext|>` y termina con `<|endoftext|>`, y se trunca a los
77 tokens de contexto de la torre de texto sin perder ese final. Un lote se rellena hasta
el más largo con el mismo `<|endoftext|>`, que es el relleno de CLIP: la torre toma el
embedding en el **primer** `<|endoftext|>` y su máscara causal impide que lo que viene
después lo cambie, así que el relleno no altera el resultado.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
from tokenizers import Tokenizer

TokenIds: TypeAlias = npt.NDArray[np.int64]

CONTEXT_LENGTH = 77
END_OF_TEXT = "<|endoftext|>"


class ClipTokenizer:
    """Convierte textos en los ids que recibe la torre de texto."""

    def __init__(self, tokenizer_file: Path) -> None:
        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        padding_id = self._tokenizer.token_to_id(END_OF_TEXT)
        self._tokenizer.enable_truncation(CONTEXT_LENGTH)
        self._tokenizer.enable_padding(pad_id=padding_id, pad_token=END_OF_TEXT)

    def encode(self, texts: Sequence[str]) -> TokenIds:
        """Los ids de `texts`, `(len(texts), L)` con `L` el más largo del lote, como mucho 77."""
        encodings = self._tokenizer.encode_batch(list(texts))
        return np.array([encoding.ids for encoding in encodings], dtype=np.int64)
