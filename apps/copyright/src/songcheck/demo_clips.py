"""Los audios de prueba: clips armados con canciones del catálogo para que una demostración
muestre siempre coincidencias, más uno sintético que no copia nada.

Se arman en memoria a partir del audio de las canciones (`ClipSources`): un trozo de cada
una, mezclado a mono a la frecuencia de la primera, y se devuelven como WAV de 16 bits. Son
deterministas: el ruido sale de una semilla fija.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import soundfile

from songcheck.sample import melody_wav

DECODE_TYPE = "float32"
WAV_FORMAT = "WAV"
WAV_SUBTYPE = "PCM_16"
MASHUP_PIECE_SECONDS = 10.0
MASHUP_FIRST_START_SECONDS = 5.0
MASHUP_SECOND_START_SECONDS = 12.5
EXCERPT_START_SECONDS = 10.0
EXCERPT_SECONDS = 15.0
NOISY_START_SECONDS = 2.5
NOISY_SECONDS = 15.0
# Ruido blanco 20 dB por debajo de la música: se oye de fondo y el MFCC aún encuentra la copia.
NOISE_SNR_DB = 30.0
NOISE_SEED = 177
SYNTHETIC_SECONDS = 15.0
SYNTHETIC_SEED = 2026
DECIBELS_PER_POWER_DECADE = 10.0


class DemoClipId(StrEnum):
    MASHUP = "mezcla"
    EXCERPT = "extracto"
    NOISY = "con-ruido"
    SYNTHETIC = "sintetico"


@dataclass(frozen=True, slots=True)
class DemoSongs:
    """Las tres canciones del catálogo con que se arman los clips: las dos de la mezcla y la
    del extracto, que es también la del clip con ruido."""

    first: int
    second: int
    excerpt: int

    @property
    def ids(self) -> tuple[int, int, int]:
        return (self.first, self.second, self.excerpt)


@dataclass(frozen=True, slots=True)
class Audio:
    samples: np.ndarray
    sample_rate: int

    @classmethod
    def decode(cls, content: bytes) -> Audio:
        samples, sample_rate = soundfile.read(
            io.BytesIO(content), dtype=DECODE_TYPE, always_2d=True
        )
        return cls(samples.mean(axis=1), int(sample_rate))

    def excerpt(self, start_seconds: float, seconds: float) -> np.ndarray:
        """`seconds` desde `start_seconds`; si la pista es más corta, el inicio se adelanta
        para que el trozo quepa entero (o es la pista entera)."""
        frames = round(seconds * self.sample_rate)
        start = min(round(start_seconds * self.sample_rate), max(0, len(self.samples) - frames))
        return self.samples[start : start + frames]

    def resampled(self, sample_rate: int) -> Audio:
        if sample_rate == self.sample_rate:
            return self
        frames = round(len(self.samples) * sample_rate / self.sample_rate)
        positions = np.linspace(0, len(self.samples) - 1, frames)
        resampled = np.interp(positions, np.arange(len(self.samples)), self.samples)
        return Audio(resampled.astype(DECODE_TYPE), sample_rate)


def wav_of(samples: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(
        buffer, np.clip(samples, -1.0, 1.0), sample_rate, format=WAV_FORMAT, subtype=WAV_SUBTYPE
    )
    return buffer.getvalue()


def with_noise(samples: np.ndarray, snr_db: float, seed: int) -> np.ndarray:
    power = float(np.mean(samples**2)) if len(samples) else 0.0
    noise_power = power / 10 ** (snr_db / DECIBELS_PER_POWER_DECADE)
    noise = np.random.default_rng(seed).normal(0.0, np.sqrt(noise_power), len(samples))
    return (samples + noise).astype(DECODE_TYPE)


def build_clip(clip: DemoClipId, songs: DemoSongs, audio: Mapping[int, bytes]) -> bytes:
    """El WAV de `clip` con el audio de las canciones de `songs` (`audio`, por id).

    Raises:
        KeyError: si falta el audio de una canción que el clip usa.
    """
    if clip is DemoClipId.SYNTHETIC:
        return melody_wav(SYNTHETIC_SECONDS, SYNTHETIC_SEED)
    if clip is DemoClipId.EXCERPT:
        source = Audio.decode(audio[songs.excerpt])
        return wav_of(source.excerpt(EXCERPT_START_SECONDS, EXCERPT_SECONDS), source.sample_rate)
    if clip is DemoClipId.NOISY:
        source = Audio.decode(audio[songs.excerpt])
        piece = source.excerpt(NOISY_START_SECONDS, NOISY_SECONDS)
        return wav_of(with_noise(piece, NOISE_SNR_DB, NOISE_SEED), source.sample_rate)
    second = Audio.decode(audio[songs.second])
    first = Audio.decode(audio[songs.first])
    joined = np.concatenate(
        [
            first.excerpt(MASHUP_FIRST_START_SECONDS, MASHUP_PIECE_SECONDS),
            second.resampled(first.sample_rate).excerpt(
                MASHUP_SECOND_START_SECONDS, MASHUP_PIECE_SECONDS
            ),
        ]
    )
    return wav_of(joined, first.sample_rate)


def songs_of_clip(clip: DemoClipId, songs: DemoSongs) -> tuple[int, ...]:
    """Las canciones del catálogo cuyo audio necesita `clip`."""
    return {
        DemoClipId.MASHUP: (songs.first, songs.second),
        DemoClipId.EXCERPT: (songs.excerpt,),
        DemoClipId.NOISY: (songs.excerpt,),
        DemoClipId.SYNTHETIC: (),
    }[clip]
