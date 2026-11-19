"""El audio que sube quien busca copias: qué es, por su contenido, y si cabe en los límites.

El nombre del archivo no cuenta: un PNG llamado `cancion.mp3` se rechaza y un WAV llamado
`cancion.mp3` se acepta como WAV. Primero se miran los primeros bytes (la firma de cada
contenedor) y después libsndfile, la librería con que el gestor lo decodifica, tiene que
abrirlo y reconocer el mismo formato. El gestor reconoce un audio por la extensión del nombre
(`src/multimedia/media.py`), así que la subida le llega con la extensión de su formato real.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus

import soundfile

from appkit import BadRequestError, ServiceError

MEBIBYTE = 1 << 20
ID3_TAG = b"ID3"
# Una trama MPEG empieza con once bits de sincronía en uno.
MPEG_SYNC_FIRST = 0xFF
MPEG_SYNC_SECOND_MASK = 0xE0
RIFF_TAG = b"RIFF"
WAVE_TAG = b"WAVE"
WAVE_TAG_OFFSET = 8
FLAC_TAG = b"fLaC"
OGG_TAG = b"OggS"
SIGNATURE_BYTES = 12


class UploadTooLargeError(ServiceError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class AudioTooLongError(ServiceError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class UnsupportedAudioError(ServiceError):
    status = HTTPStatus.UNSUPPORTED_MEDIA_TYPE


class CorruptAudioError(BadRequestError):
    pass


class AudioTooShortError(BadRequestError):
    pass


class AudioFormat(Enum):
    """Los formatos que se aceptan: la extensión con que se envían al gestor, su tipo MIME y
    los nombres con que libsndfile los reconoce (`WAVEX` es el WAV extensible)."""

    WAV = (".wav", "audio/wav", frozenset({"WAV", "WAVEX"}))
    FLAC = (".flac", "audio/flac", frozenset({"FLAC"}))
    OGG = (".ogg", "audio/ogg", frozenset({"OGG"}))
    MP3 = (".mp3", "audio/mpeg", frozenset({"MP3"}))

    def __init__(self, extension: str, content_type: str, decoder_names: frozenset[str]) -> None:
        self.extension = extension
        self.content_type = content_type
        self.decoder_names = decoder_names


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """El tamaño y las duraciones que se admiten."""

    max_bytes: int
    min_seconds: float
    max_seconds: float


@dataclass(frozen=True, slots=True)
class ProbedAudio:
    """Un audio subido, reconocido por su contenido, con lo que dice su cabecera."""

    content: bytes
    format: AudioFormat
    duration_seconds: float
    sample_rate: int
    channels: int


def _check_size(content: bytes, max_bytes: int) -> None:
    """Raises:
    UploadTooLargeError: si `content` pesa más de `max_bytes`.
    CorruptAudioError: si llegó vacío.
    """
    if len(content) > max_bytes:
        raise UploadTooLargeError(
            f"el audio pesa más de {max_bytes / MEBIBYTE:g} MiB; sube uno más liviano"
        )
    if not content:
        raise CorruptAudioError("el archivo llegó vacío")


def probe_audio(content: bytes, limits: UploadLimits) -> ProbedAudio:
    """Reconoce el audio por su firma y su cabecera y comprueba los límites.

    Raises:
        UploadTooLargeError: si pesa más de `limits.max_bytes`.
        UnsupportedAudioError: si la firma no es la de MP3, WAV, FLAC u OGG, o libsndfile
            reconoce otro formato.
        CorruptAudioError: si llegó vacío o la firma es buena y libsndfile no lo abre.
        AudioTooLongError: si dura más de `limits.max_seconds`.
        AudioTooShortError: si dura menos de `limits.min_seconds`.
    """
    _check_size(content, limits.max_bytes)
    claimed = format_of_signature(content[:SIGNATURE_BYTES])
    try:
        info = soundfile.info(io.BytesIO(content))
    except (soundfile.SoundFileError, RuntimeError) as error:
        raise CorruptAudioError(f"el audio parece {claimed.name} pero no se puede abrir") from error
    if info.format not in claimed.decoder_names:
        raise UnsupportedAudioError(
            f"el audio empieza como {claimed.name} pero su contenido es {info.format}"
        )
    probed = ProbedAudio(content, claimed, float(info.duration), info.samplerate, info.channels)
    _check_duration(probed.duration_seconds, limits)
    return probed


def format_of_signature(head: bytes) -> AudioFormat:
    """El formato que anuncian los primeros bytes.

    Raises:
        UnsupportedAudioError: si no son los de ningún formato aceptado.
    """
    for matches, audio_format in _SIGNATURES:
        if matches(head):
            return audio_format
    raise UnsupportedAudioError(
        "el archivo no es un audio MP3, WAV, FLAC ni OGG (se revisa su contenido, no su nombre)"
    )


def _is_wave(head: bytes) -> bool:
    return head.startswith(RIFF_TAG) and head[WAVE_TAG_OFFSET:].startswith(WAVE_TAG)


def _is_mpeg(head: bytes) -> bool:
    if head.startswith(ID3_TAG):
        return True
    return (
        len(head) > 1
        and head[0] == MPEG_SYNC_FIRST
        and head[1] & MPEG_SYNC_SECOND_MASK == MPEG_SYNC_SECOND_MASK
    )


_SIGNATURES: tuple[tuple[Callable[[bytes], bool], AudioFormat], ...] = (
    (_is_wave, AudioFormat.WAV),
    (lambda head: head.startswith(FLAC_TAG), AudioFormat.FLAC),
    (lambda head: head.startswith(OGG_TAG), AudioFormat.OGG),
    (_is_mpeg, AudioFormat.MP3),
)


def _check_duration(seconds: float, limits: UploadLimits) -> None:
    if seconds > limits.max_seconds:
        raise AudioTooLongError(
            f"el audio dura {seconds:.1f} s y se admiten hasta {limits.max_seconds:g} s"
        )
    if seconds < limits.min_seconds:
        raise AudioTooShortError(
            f"el audio dura {seconds:.2f} s y hace falta al menos {limits.min_seconds:g} s"
        )
