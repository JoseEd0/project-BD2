"""Parámetros del pipeline de rostros, con sus valores por omisión y su porqué."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Cómo se decodifica una foto y cómo se buscan sus rostros.

    Attributes:
        max_image_pixels: píxeles que puede declarar una imagen. 50 millones admiten la foto
            completa de un sensor de 48 MP y rechazan, antes de decodificarla, una «bomba»
            que con pocos bytes promete gigapíxeles; decodificada en BGR ocupa hasta 150 MB.
        detection_max_side: lado mayor con que la imagen entra a YuNet. Una foto de 12 MP
            se reduce a 640 px: un rostro que ocupe la vigésima parte del ancho conserva
            unos 30 px, de sobra para el detector, y la red cuesta lo mismo con cualquier foto.
        detection_min_side: lado mayor al que se amplía una imagen chica antes de detectar.
            YuNet no encuentra el rostro de un recorte de 64 px y sí el mismo recorte llevado
            a 320 px (medido con las fotos de los tests).
        score_threshold: confianza mínima de una detección; 0,9 es el valor de OpenCV Zoo.
        nms_threshold: solapamiento (IoU) a partir del cual dos cajas son el mismo rostro.
        top_k: candidatos que YuNet conserva antes de la supresión de no máximos.
    """

    max_image_pixels: int = 50_000_000
    detection_max_side: int = 640
    detection_min_side: int = 320
    score_threshold: float = 0.9
    nms_threshold: float = 0.3
    top_k: int = 5000
