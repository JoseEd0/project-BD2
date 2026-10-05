"""Descarga los puntos de interés reales de un país y los deja en un CSV que el gestor carga.

Los datos son de **OpenStreetMap**: tiendas, gasolineras, farmacias, colegios, bancos… cada
uno con su nombre, su rubro y su ubicación. Para el Perú son unos 128 000 lugares. Salen
del extracto diario que publica Geofabrik en formato shapefile, un archivo de cientos de MB
del que aquí solo se piden por HTTP los bytes de las capas que hacen falta (unos 5 MB).

Solo se conservan los puntos **con nombre**: OpenStreetMap también cataloga bancas,
papeleras y cámaras, que no son lugares que alguien vaya a buscar.

    .venv/bin/python demos/descargar_lugares.py --salida data/datasets/lugares_peru.csv
    .venv/bin/python demos/descargar_lugares.py --salida data/datasets/lugares_lima.csv \\
        --recuadro -12.52 -77.20 -11.57 -76.62

El CSV tiene las columnas `id, nombre, rubro, ubicacion`, con la ubicación escrita como
`POINT(latitud, longitud)`: se carga tal cual desde **Cargar CSV** o con
`CREATE TABLE lugares FROM FILE '…' USING INDEX RTREE("ubicacion")`.

Licencia de los datos: ODbL, © colaboradores de OpenStreetMap (openstreetmap.org/copyright).
"""

from __future__ import annotations

import argparse
import csv
import io
import struct
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

DEFAULT_URL = "https://download.geofabrik.de/south-america/peru-latest-free.shp.zip"
# Capas de puntos que se leen y, de cada una, los rubros que interesan (`None`: todos). De
# la capa de tráfico solo las gasolineras: el resto son semáforos y pasos de cebra.
LAYERS: dict[str, frozenset[str] | None] = {
    "gis_osm_pois_free_1": None,
    "gis_osm_traffic_free_1": frozenset({"fuel"}),
}
USER_AGENT = "minigestor-bd2 (proyecto universitario)"
TIMEOUT_SECONDS = 180
READ_AHEAD_BYTES = 4 * 1024 * 1024
DEFAULT_ENCODING = "utf-8"
COORDINATE_DECIMALS = 6
HEADER = ("id", "nombre", "rubro", "ubicacion")

SHP_HEADER_BYTES = 100
SHP_RECORD_HEADER = struct.Struct(">ii")
SHP_SHAPE_TYPE = struct.Struct("<i")
SHP_POINT = struct.Struct("<dd")
SHP_POINT_TYPE = 1
BYTES_PER_WORD = 2

DBF_HEADER = struct.Struct("<4xIHH")
DBF_FIELD_BYTES = 32
DBF_FIELD = struct.Struct("<11sc4xB")
DBF_FIELDS_END = b"\r"
DBF_DELETED = b"*"

# Rubros de OpenStreetMap que la demo nombra en castellano; el resto conserva su etiqueta.
RUBROS = {
    "fuel": "gasolinera",
    "pharmacy": "farmacia",
    "restaurant": "restaurante",
    "fast_food": "comida rápida",
    "cafe": "cafetería",
    "bank": "banco",
    "atm": "cajero",
    "supermarket": "supermercado",
    "convenience": "bodega",
    "school": "colegio",
    "university": "universidad",
    "hospital": "hospital",
    "clinic": "clínica",
    "hotel": "hotel",
    "bakery": "panadería",
    "police": "comisaría",
    "kindergarten": "jardín infantil",
    "doctors": "consultorio",
    "dentist": "dentista",
    "hostel": "hostal",
    "doityourself": "ferretería",
    "clothes": "ropa",
    "hairdresser": "peluquería",
    "butcher": "carnicería",
    "library": "biblioteca",
    "bookshop": "librería",
    "bar": "bar",
    "pub": "bar",
    "museum": "museo",
    "viewpoint": "mirador",
    "archaeological": "sitio arqueológico",
    "ruins": "ruinas",
    "attraction": "atractivo turístico",
    "market_place": "mercado",
    "mall": "centro comercial",
    "post_office": "correo",
    "fire_station": "bomberos",
    "veterinary": "veterinaria",
    "optician": "óptica",
    "car_repair": "taller mecánico",
    "guesthouse": "hospedaje",
    "motel": "motel",
    "camp_site": "camping",
    "caravan_site": "camping",
    "alpine_hut": "refugio",
    "wilderness_hut": "refugio",
    "shelter": "refugio",
    "chalet": "chalet",
    "beauty_shop": "salón de belleza",
    "travel_agent": "agencia de viajes",
    "laundry": "lavandería",
    "mobile_phone_shop": "tienda de celulares",
    "computer_shop": "tienda de cómputo",
    "shoe_shop": "zapatería",
    "furniture_shop": "mueblería",
    "sports_shop": "tienda deportiva",
    "outdoor_shop": "tienda de camping",
    "bicycle_shop": "tienda de bicicletas",
    "gift_shop": "tienda de regalos",
    "toy_shop": "juguetería",
    "department_store": "tienda por departamentos",
    "general": "tienda",
    "kiosk": "quiosco",
    "newsagent": "quiosco",
    "beverages": "licorería",
    "stationery": "papelería",
    "chemist": "perfumería",
    "jeweller": "joyería",
    "greengrocer": "verdulería",
    "florist": "florería",
    "garden_centre": "vivero",
    "car_wash": "lavado de autos",
    "car_dealership": "concesionario",
    "car_rental": "alquiler de autos",
    "bicycle_rental": "alquiler de bicicletas",
    "food_court": "patio de comidas",
    "nightclub": "discoteca",
    "cinema": "cine",
    "theatre": "teatro",
    "arts_centre": "centro cultural",
    "community_centre": "centro comunal",
    "town_hall": "municipalidad",
    "courthouse": "juzgado",
    "embassy": "embajada",
    "college": "instituto",
    "marketplace": "mercado",
    "fitness_centre": "gimnasio",
    "sports_centre": "centro deportivo",
    "stadium": "estadio",
    "swimming_pool": "piscina",
    "pitch": "cancha",
    "park": "parque",
    "playground": "juegos infantiles",
    "zoo": "zoológico",
    "theme_park": "parque de diversiones",
    "tourist_info": "información turística",
    "artwork": "obra de arte",
    "monument": "monumento",
    "memorial": "memorial",
    "fountain": "fuente",
    "lighthouse": "faro",
    "wayside_shrine": "capilla",
    "wayside_cross": "cruz",
    "drinking_water": "bebedero",
    "water_well": "pozo",
    "toilet": "baños",
    "telephone": "teléfono público",
    "post_box": "buzón",
    "graveyard": "cementerio",
    "nursing_home": "asilo",
    "prison": "penal",
    "public_building": "edificio público",
    "comms_tower": "torre de comunicaciones",
}


@dataclass(frozen=True, slots=True)
class Place:
    """Un punto de interés: su identificador en OpenStreetMap, su rubro y dónde está."""

    identifier: int
    name: str
    category: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class Box:
    """Recuadro de latitudes y longitudes al que se limita la descarga."""

    south: float
    west: float
    north: float
    east: float

    def contains(self, place: Place) -> bool:
        return (
            self.south <= place.latitude <= self.north
            and self.west <= place.longitude <= self.east
        )


class RemoteFile(io.RawIOBase):
    """Archivo remoto de solo lectura que pide por HTTP solo los bytes que se leen.

    Es lo que permite abrir un `.zip` de cientos de MB y sacar de él una capa de unos
    pocos: `zipfile` lee el índice del final y después únicamente los archivos pedidos.
    """

    def __init__(self, url: str) -> None:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            self._url = response.url
            self._size = int(response.headers["Content-Length"])
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        origin = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._size}[whence]
        self._position = origin + offset
        return self._position

    def readinto(self, buffer: Any, /) -> int:
        if self._position >= self._size:
            return 0
        last = min(self._position + len(buffer), self._size) - 1
        request = urllib.request.Request(
            self._url,
            headers={"Range": f"bytes={self._position}-{last}", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            received = response.read()
        buffer[: len(received)] = received
        self._position += len(received)
        return len(received)


def read_points(shp: BinaryIO) -> Iterator[tuple[float, float] | None]:
    """Latitud y longitud de cada registro de un `.shp` de puntos; `None` si no tiene forma.

    Un shapefile guarda primero la longitud (X) y luego la latitud (Y).
    """
    shp.seek(SHP_HEADER_BYTES)
    while header := shp.read(SHP_RECORD_HEADER.size):
        _, content_words = SHP_RECORD_HEADER.unpack(header)
        content = shp.read(content_words * BYTES_PER_WORD)
        if SHP_SHAPE_TYPE.unpack_from(content)[0] != SHP_POINT_TYPE:
            yield None
            continue
        longitude, latitude = SHP_POINT.unpack_from(content, SHP_SHAPE_TYPE.size)
        yield latitude, longitude


def read_attributes(dbf: BinaryIO, encoding: str) -> Iterator[dict[str, str] | None]:
    """Atributos de cada registro de un `.dbf`, por nombre de campo; `None` si está borrado."""
    record_count, header_bytes, record_bytes = DBF_HEADER.unpack(dbf.read(DBF_HEADER.size))
    dbf.seek(DBF_FIELD_BYTES)
    fields: list[tuple[str, int]] = []
    while (descriptor := dbf.read(DBF_FIELD_BYTES))[:1] != DBF_FIELDS_END:
        name, _, length = DBF_FIELD.unpack_from(descriptor)
        fields.append((name.split(b"\x00", 1)[0].decode("ascii"), length))
    dbf.seek(header_bytes)
    for _ in range(record_count):
        record = dbf.read(record_bytes)
        if record[:1] == DBF_DELETED:
            yield None
            continue
        values: dict[str, str] = {}
        offset = 1
        for name, length in fields:
            values[name] = record[offset : offset + length].decode(encoding, "replace").strip()
            offset += length
        yield values


def read_places(archive: zipfile.ZipFile) -> Iterator[Place]:
    """Lugares con nombre de las capas de `LAYERS`, sin repetir identificador.

    Un mismo nodo de OpenStreetMap puede figurar con dos rubros; se queda el primero, para
    que el identificador sirva de clave primaria.
    """
    seen: set[int] = set()
    for layer, wanted in LAYERS.items():
        for place, kind in _layer_places(archive, layer):
            if not place.name or place.identifier in seen:
                continue
            if wanted is not None and kind not in wanted:
                continue
            seen.add(place.identifier)
            yield place


def _layer_places(archive: zipfile.ZipFile, layer: str) -> Iterator[tuple[Place, str]]:
    """Cada punto de una capa, con su rubro tal como lo etiqueta OpenStreetMap."""
    encoding = _encoding_of(archive, layer)
    with archive.open(f"{layer}.shp") as shp, archive.open(f"{layer}.dbf") as dbf:
        points = read_points(io.BytesIO(shp.read()))
        attributes = read_attributes(io.BytesIO(dbf.read()), encoding)
        for point, values in zip(points, attributes, strict=True):
            if point is None or values is None:
                continue
            kind = values["fclass"]
            place = Place(
                identifier=int(values["osm_id"]),
                name=values["name"],
                category=RUBROS.get(kind, kind),
                latitude=point[0],
                longitude=point[1],
            )
            yield place, kind


def write_csv(places: Iterator[Place], path: Path) -> int:
    """Escribe los lugares en el formato que carga el gestor. Devuelve cuántos escribió."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", newline="", encoding=DEFAULT_ENCODING) as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for place in places:
            location = (
                f"POINT({place.latitude:.{COORDINATE_DECIMALS}f}, "
                f"{place.longitude:.{COORDINATE_DECIMALS}f})"
            )
            writer.writerow((place.identifier, place.name, place.category, location))
            written += 1
    return written


def _encoding_of(archive: zipfile.ZipFile, layer: str) -> str:
    """Codificación que declara la capa en su `.cpg`; UTF-8 si no trae ninguno."""
    name = f"{layer}.cpg"
    if name not in archive.namelist():
        return DEFAULT_ENCODING
    return archive.read(name).decode("ascii").strip() or DEFAULT_ENCODING


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--salida", type=Path, required=True, help="CSV que se escribe")
    parser.add_argument("--url", default=DEFAULT_URL, help="shapefile de Geofabrik de un país")
    parser.add_argument(
        "--recuadro",
        type=float,
        nargs=4,
        metavar=("SUR", "OESTE", "NORTE", "ESTE"),
        help="solo los puntos dentro de este recuadro de coordenadas",
    )
    arguments = parser.parse_args()
    remote = io.BufferedReader(RemoteFile(arguments.url), buffer_size=READ_AHEAD_BYTES)
    with zipfile.ZipFile(remote) as archive:
        places = read_places(archive)
        if arguments.recuadro is not None:
            box = Box(*arguments.recuadro)
            places = (place for place in places if box.contains(place))
        written = write_csv(places, arguments.salida)
    print(f"{written} lugares en {arguments.salida}")
    print("Datos de OpenStreetMap, licencia ODbL: © colaboradores de OpenStreetMap")


if __name__ == "__main__":
    main()
