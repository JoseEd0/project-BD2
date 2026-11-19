"""Las distorsiones con que se intenta esconder una copia, cada una por separado.

Todas reciben y devuelven muestras mono de `float32`. `time_scale` dice cuánto se estira la
duración: los tramos de la verdad, medidos en el remix sin distorsionar, se multiplican por
él para compararlos con lo que localiza la detección.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass

import librosa
import numpy as np
import soundfile

DECIBELS_PER_POWER_DECADE = 10.0
DECIBELS_PER_AMPLITUDE_DECADE = 20.0
MP3_FORMAT = "MP3"
CONSTANT_BITRATE = "CONSTANT"
# libsndfile elige el bitrate de un MP3 de tasa constante por un nivel de compresión de 0
# (el más alto) a 1 (el más bajo), lineal entre los extremos de la versión de MPEG que toca a
# la frecuencia, y LAME redondea al bitrate permitido más cercano. MPEG-1 cubre 32 a 48 kHz
# con 32 a 320 kbit/s; MPEG-2, 16 a 24 kHz con 8 a 160 kbit/s (ISO/IEC 11172-3 y 13818-3).
MPEG1_LOWEST_RATE = 32_000
MPEG1_KBPS = (32.0, 320.0)
MPEG2_KBPS = (8.0, 160.0)

Transform = Callable[[np.ndarray, int, np.random.Generator], np.ndarray]


@dataclass(frozen=True, slots=True)
class Distortion:
    """Una distorsión: su nombre en los resultados, cómo se aplica y cuánto estira el audio."""

    name: str
    transform: Transform
    time_scale: float = 1.0

    def apply(self, samples: np.ndarray, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
        return np.asarray(self.transform(samples, sample_rate, rng), dtype=np.float32)


def unchanged() -> Distortion:
    return Distortion("original", lambda samples, _rate, _rng: samples)


def noise(name: str, snr_db: float) -> Distortion:
    """Ruido blanco gaussiano con la relación señal-ruido `snr_db` sobre todo el remix."""
    return Distortion(name, lambda samples, _rate, rng: with_noise(samples, snr_db, rng))


def speed(name: str, rate: float) -> Distortion:
    """El tempo multiplicado por `rate` sin cambiar el tono (vocoder de fase)."""
    return Distortion(name, lambda samples, _rate, _rng: time_stretched(samples, rate), 1 / rate)


def pitch(name: str, semitones: float) -> Distortion:
    """El tono movido `semitones` sin cambiar la duración."""
    return Distortion(
        name, lambda samples, sample_rate, _rng: pitch_shifted(samples, sample_rate, semitones)
    )


def mp3(name: str, kbps: float) -> Distortion:
    """Codificado en MP3 a `kbps` constantes y decodificado otra vez."""
    return Distortion(
        name, lambda samples, sample_rate, _rng: reencoded(samples, sample_rate, kbps)
    )


def low_pass(name: str, cutoff_hz: float) -> Distortion:
    """Todo lo que está por encima de `cutoff_hz`, quitado."""
    return Distortion(
        name, lambda samples, sample_rate, _rng: low_passed(samples, sample_rate, cutoff_hz)
    )


def volume(name: str, gain_db: float) -> Distortion:
    return Distortion(name, lambda samples, _rate, _rng: with_gain(samples, gain_db))


def with_noise(samples: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    noise_power = power(samples) / 10 ** (snr_db / DECIBELS_PER_POWER_DECADE)
    return samples + rng.normal(0.0, np.sqrt(noise_power), len(samples)).astype(np.float32)


def time_stretched(samples: np.ndarray, rate: float) -> np.ndarray:
    return np.asarray(librosa.effects.time_stretch(samples, rate=rate))


def pitch_shifted(samples: np.ndarray, sample_rate: int, semitones: float) -> np.ndarray:
    return np.asarray(librosa.effects.pitch_shift(samples, sr=sample_rate, n_steps=semitones))


def reencoded(samples: np.ndarray, sample_rate: int, kbps: float) -> np.ndarray:
    """El audio tras pasar por un MP3 de `kbps` constantes, recortado o completado con ceros a
    su largo original."""
    decoded, _ = soundfile.read(io.BytesIO(mp3_bytes(samples, sample_rate, kbps)), dtype="float32")
    fitted = np.zeros(len(samples), dtype=np.float32)
    fitted[: min(len(decoded), len(samples))] = decoded[: len(samples)]
    return fitted


def mp3_bytes(samples: np.ndarray, sample_rate: int, kbps: float) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(
        buffer,
        samples,
        sample_rate,
        format=MP3_FORMAT,
        compression_level=mp3_compression_level(kbps, sample_rate),
        bitrate_mode=CONSTANT_BITRATE,
    )
    return buffer.getvalue()


def mp3_compression_level(kbps: float, sample_rate: int) -> float:
    lowest, highest = MPEG1_KBPS if sample_rate >= MPEG1_LOWEST_RATE else MPEG2_KBPS
    return (highest - kbps) / (highest - lowest)


def low_passed(samples: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    """Filtro ideal en frecuencia: la FFT del audio entero sin las componentes de más de
    `cutoff_hz`."""
    spectrum = np.fft.rfft(samples)
    spectrum[np.fft.rfftfreq(len(samples), 1 / sample_rate) > cutoff_hz] = 0
    return np.fft.irfft(spectrum, len(samples))


def with_gain(samples: np.ndarray, gain_db: float) -> np.ndarray:
    return samples * 10 ** (gain_db / DECIBELS_PER_AMPLITUDE_DECADE)


def power(samples: np.ndarray) -> float:
    return float(np.mean(np.square(samples, dtype=np.float64)))


def snr_db(clean: np.ndarray, distorted: np.ndarray) -> float:
    """La relación señal-ruido medida entre un audio y su versión con ruido."""
    return DECIBELS_PER_POWER_DECADE * float(np.log10(power(clean) / power(distorted - clean)))
