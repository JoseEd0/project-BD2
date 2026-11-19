"""El texto del consentimiento que acepta quien se registra, y su versión.

Un rostro es un dato biométrico, y la Ley 29733 de Protección de Datos Personales lo trata
como dato sensible: su tratamiento pide un consentimiento previo, expreso, informado y por
escrito. La versión sale del propio texto (con el plazo de conservación ya escrito): si
cambia una palabra o el plazo configurado, cambia la versión, y un registro que aceptó el
texto anterior no pasa.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

CONSENT_REVISION = 1
VERSION_DIGEST_CHARACTERS = 12
TITLE = "Consentimiento para registrar tu rostro"
PURPOSE = (
    "Reconocerte en esta demostración de reconocimiento facial del curso Base de Datos 2: "
    "cuando apuntes la cámara a tu cara, mostrar tu nombre."
)
STORED_DATA = (
    "El nombre que escribas.",
    "Una huella numérica de tu rostro por cada toma (128 números por toma), de la que no "
    "se puede reconstruir tu foto.",
    "La versión de este texto y la fecha en que lo aceptaste.",
)
NOT_STORED = "Tus fotos: se analizan en memoria y se descartan en el momento."
RIGHTS = (
    "Puedes ver y borrar tu registro cuando quieras con el código que recibes al terminar. "
    "Si cancelas un registro a medias, se borra lo que se haya tomado. Nadie más recibe tus "
    "datos."
)


@dataclass(frozen=True, slots=True)
class ConsentText:
    """El consentimiento tal como se muestra y se acepta."""

    version: str
    title: str
    purpose: str
    stored_data: tuple[str, ...]
    not_stored: str
    retention_days: int
    retention: str
    rights: str
    text: str


def consent_text(retention_days: int) -> ConsentText:
    """El consentimiento con un plazo de conservación de `retention_days` días."""
    retention = (
        f"Tu registro se borra solo a los {retention_days} días de creado, o antes si lo pides."
    )
    stored = "\n".join(f"- {item}" for item in STORED_DATA)
    text = (
        f"{TITLE}\n\nPara qué: {PURPOSE}\n\nQué se guarda:\n{stored}\n\nQué no se guarda: "
        f"{NOT_STORED}\n\nCuánto tiempo: {retention}\n\nTus derechos: {RIGHTS}"
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:VERSION_DIGEST_CHARACTERS]
    return ConsentText(
        version=f"{CONSENT_REVISION}-{digest}",
        title=TITLE,
        purpose=PURPOSE,
        stored_data=STORED_DATA,
        not_stored=NOT_STORED,
        retention_days=retention_days,
        retention=retention,
        rights=RIGHTS,
        text=text,
    )
