"""Un video Y4M de una foto quieta, para la cámara simulada de Chromium
(`--use-file-for-fake-video-capture`): la suite e2e (`e2e/run.sh`) registra y reconoce con él
un rostro real contra el servicio de verdad.

    python -m apps.faces.tests.fake_camera <foto> <video.y4m> [izquierda arriba derecha abajo]

El cuadro es de 1 280 x 720, lo que pide la PWA a la cámara, con la foto (o el recorte dado,
en píxeles) escalada al alto del cuadro y centrada: recortada a la cara, como una selfie,
queda dentro del óvalo y con el tamaño que la compuerta acepta. Chromium repite el archivo en
bucle, así que basta un cuadro.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
FRAMES_PER_SECOND = 30
BACKGROUND_GRAY = (128, 128, 128)
# 4:2:0 con el rango completo de JPEG: el YCbCr de Pillow, con el croma a la mitad por lado.
Y4M_HEADER = f"YUV4MPEG2 W{FRAME_WIDTH} H{FRAME_HEIGHT} F{FRAMES_PER_SECOND}:1 Ip A1:1 C420jpeg\n"
FRAME_MARKER = b"FRAME\n"


Crop = tuple[int, int, int, int]


def still_frame(photo: Path, crop: Crop | None = None) -> Image.Image:
    portrait = Image.open(photo).convert("RGB")
    if crop is not None:
        portrait = portrait.crop(crop)
    width = round(portrait.width * FRAME_HEIGHT / portrait.height)
    scaled = portrait.resize((width, FRAME_HEIGHT), Image.Resampling.LANCZOS)
    frame = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), BACKGROUND_GRAY)
    frame.paste(scaled, ((FRAME_WIDTH - width) // 2, 0))
    return frame


def y4m_bytes(frame: Image.Image) -> bytes:
    luma, blue, red = frame.convert("YCbCr").split()
    chroma_size = (frame.width // 2, frame.height // 2)
    planes = [luma] + [plane.resize(chroma_size, Image.Resampling.BOX) for plane in (blue, red)]
    return Y4M_HEADER.encode("ascii") + FRAME_MARKER + b"".join(plane.tobytes() for plane in planes)


def write_fake_camera(photo: Path, video: Path, crop: Crop | None = None) -> Path:
    """Escribe en `video` un Y4M de un cuadro con `photo` (o su recorte `crop`) centrada y
    lo devuelve."""
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(y4m_bytes(still_frame(photo, crop)))
    return video


def _crop_argument(values: list[str]) -> Crop | None:
    if not values:
        return None
    left, top, right, bottom = (int(value) for value in values)
    return left, top, right, bottom


if __name__ == "__main__":
    print(write_fake_camera(Path(sys.argv[1]), Path(sys.argv[2]), _crop_argument(sys.argv[3:])))
