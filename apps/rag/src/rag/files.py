"""Escritura atómica de los archivos del corpus y huella de un texto.

Todo se escribe primero en `<nombre>.parcial` (el sufijo de `appkit.download`) y se
renombra al terminar: una corrida
cortada deja el archivo anterior o ninguno, nunca uno a medias que la siguiente tome por
completo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from appkit.download import PARTIAL_SUFFIX

ENCODING = "utf-8"


def write_bytes_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    partial.write_bytes(content)
    partial.replace(path)


def write_json(path: Path, value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    write_bytes_atomically(path, text.encode(ENCODING))


def write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    write_bytes_atomically(path, text.encode(ENCODING))


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding=ENCODING))


def read_jsonl(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding=ENCODING) as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode(ENCODING)).hexdigest()
