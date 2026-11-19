"""Un FMA de juguete para los tests: `tracks.csv`, los dos `.zip` con el formato que publica el
FMA y un servidor HTTP local que los sirve por rangos y cuenta lo que le piden."""

from __future__ import annotations

import csv
import io
import threading
import zipfile
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from songcheck.fma.archive import track_path
from songcheck.fma.prepare import FmaSources
from songcheck.sample import melody_wav

LOCALHOST = "127.0.0.1"
POLL_SECONDS = 0.01
KEEP_ALIVE_PROTOCOL = "HTTP/1.1"
AUDIO_PATH = "/fma_small.zip"
METADATA_PATH = "/fma_metadata.zip"
# Cuatro segundos en PCM de 16 bits a 16 kHz pesan 128 KB, más que el mínimo de una pista.
TRACK_SECONDS = 4.0
HEADER = [
    ["", "artist", "set", "set", "track", "track"],
    ["", "name", "split", "subset", "genre_top", "title"],
    ["track_id", "", "", "", "", ""],
]


@dataclass(frozen=True)
class ToyTrack:
    track_id: int
    title: str
    artist: str
    genre: str
    subset: str = "small"
    seconds: float = TRACK_SECONDS


TOY_TRACKS = (
    ToyTrack(2, "  Food  ", "AWOL", "Hip-Hop"),
    ToyTrack(5, "This World", "AWOL", "Hip-Hop"),
    ToyTrack(140, "Queen  of the Wires", "Alec K. Redfearn", "Folk"),
    ToyTrack(155066, "Final", "Nadie", "Rock", seconds=3.5),
    ToyTrack(99134, "Sin audio", "Nadie", "Rock"),
    ToyTrack(7, "De otro subconjunto", "Otro", "Rock", subset="medium"),
)
SILENT_TRACK = 99134
SILENT_BYTES = 1_500


def wav_bytes(seconds: float, seed: int) -> bytes:
    """Un WAV, que libsndfile reconoce por su cabecera aunque se llame `.mp3`."""
    return melody_wav(seconds, seed)


def tracks_csv(tracks: Sequence[ToyTrack]) -> bytes:
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerows(HEADER)
    for track in tracks:
        writer.writerow(
            [track.track_id, track.artist, "training", track.subset, track.genre, track.title]
        )
    return text.getvalue().encode()


def audio_members(tracks: Sequence[ToyTrack]) -> dict[str, bytes]:
    members = {"fma_small/README.txt": b"FMA small"}
    for track in tracks:
        if track.subset != "small":
            continue
        name = f"fma_small/{track_path(track.track_id)}"
        if track.track_id == SILENT_TRACK:
            members[name] = b"\0" * SILENT_BYTES
        else:
            members[name] = wav_bytes(track.seconds, track.track_id)
    return members


def zip_of(members: dict[str, bytes]) -> bytes:
    """Un `.zip` alternando miembros sin comprimir y con bzip2, como el del FMA."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index, (name, data) in enumerate(members.items()):
            method = zipfile.ZIP_BZIP2 if index % 2 else zipfile.ZIP_STORED
            archive.writestr(name, data, compress_type=method)
    return buffer.getvalue()


def metadata_zip(tracks: Sequence[ToyTrack]) -> bytes:
    return zip_of(
        {"fma_metadata/README.txt": b"FMA", "fma_metadata/tracks.csv": tracks_csv(tracks)}
    )


@dataclass
class ToyServer:
    """Los archivos que sirve, por ruta, y cuántos GET recibió cada una."""

    files: dict[str, bytes]
    gets: Counter[str] = field(default_factory=Counter)
    base_url: str = ""

    def sources(self) -> FmaSources:
        return FmaSources(
            audio_url=self.base_url + AUDIO_PATH, metadata_url=self.base_url + METADATA_PATH
        )


def handler_for(server: ToyServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = KEEP_ALIVE_PROTOCOL

        def do_HEAD(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", str(len(server.files[self.path])))
            self.end_headers()

        def do_GET(self) -> None:
            server.gets[self.path] += 1
            content = server.files[self.path]
            first, _, last = self.headers["Range"].removeprefix("bytes=").partition("-")
            body = content[int(first) : int(last) + 1]
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return

    return Handler


@contextmanager
def serving(files: dict[str, bytes]) -> Iterator[ToyServer]:
    toy = ToyServer(files)
    http_server = ThreadingHTTPServer((LOCALHOST, 0), handler_for(toy))
    thread = threading.Thread(target=http_server.serve_forever, args=(POLL_SECONDS,), daemon=True)
    thread.start()
    host, port = http_server.server_address[:2]
    toy.base_url = f"http://{host!s}:{port}"
    try:
        yield toy
    finally:
        http_server.shutdown()
        http_server.server_close()


def toy_audio(tracks: Sequence[ToyTrack] = TOY_TRACKS) -> dict[int, bytes]:
    """Los bytes de cada pista de audio del `.zip` de juguete, por id."""
    return {
        int(name.removesuffix(".mp3").rsplit("/", 1)[1]): data
        for name, data in audio_members(tracks).items()
        if name.endswith(".mp3")
    }


def toy_fma(tracks: Sequence[ToyTrack] = TOY_TRACKS) -> dict[str, bytes]:
    return {AUDIO_PATH: zip_of(audio_members(tracks)), METADATA_PATH: metadata_zip(tracks)}
