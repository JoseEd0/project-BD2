"""Qué licencias de arXiv permiten que la aplicación sirva el PDF.

arXiv publica cada envío con la licencia que eligió su autor. La que se usa por omisión,
«arXiv non-exclusive license to distribute», solo autoriza a arXiv a distribuirlo, así que
el corpus no la acepta. Se aceptan CC BY, CC BY-SA y CC0 (y la antigua «public domain» de
Creative Commons): permiten copiar, redistribuir y adaptar, también con fines comerciales.
Las variantes NC y ND quedan fuera: los fragmentos son una adaptación del texto y la
aplicación no tiene por qué ser no comercial.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LicenseFamily:
    """Una familia de licencias, reconocida por la ruta de su URL sin la versión."""

    label: str
    pattern: re.Pattern[str]
    redistributable: bool


CREATIVE_COMMONS = r"creativecommons\.org/"
FAMILIES = (
    LicenseFamily("CC BY", re.compile(CREATIVE_COMMONS + r"licenses/by/"), True),
    LicenseFamily("CC BY-SA", re.compile(CREATIVE_COMMONS + r"licenses/by-sa/"), True),
    LicenseFamily("CC0", re.compile(CREATIVE_COMMONS + r"publicdomain/zero/"), True),
    LicenseFamily(
        "dominio público", re.compile(CREATIVE_COMMONS + r"licenses/publicdomain/?$"), True
    ),
    LicenseFamily("CC BY-NC", re.compile(CREATIVE_COMMONS + r"licenses/by-nc/"), False),
    LicenseFamily("CC BY-NC-SA", re.compile(CREATIVE_COMMONS + r"licenses/by-nc-sa/"), False),
    LicenseFamily("CC BY-NC-ND", re.compile(CREATIVE_COMMONS + r"licenses/by-nc-nd/"), False),
    LicenseFamily("CC BY-ND", re.compile(CREATIVE_COMMONS + r"licenses/by-nd/"), False),
    LicenseFamily(
        "arXiv no exclusiva", re.compile(r"arxiv\.org/licenses/nonexclusive-distrib/"), False
    ),
)
UNKNOWN_LICENSE = "otra"
MISSING_LICENSE = "sin licencia"


def license_family(url: str | None) -> str:
    """El nombre de la familia de la licencia `url` (`"CC BY"`, `"arXiv no exclusiva"`…),
    `"sin licencia"` si no la hay y `"otra"` si no se reconoce."""
    family = _match(url)
    if family is not None:
        return family.label
    return MISSING_LICENSE if not url else UNKNOWN_LICENSE


def is_redistributable(url: str | None) -> bool:
    """Si la licencia `url` deja copiar y adaptar el paper sin pedir permiso. Una licencia
    ausente o desconocida no se acepta."""
    family = _match(url)
    return family is not None and family.redistributable


def _match(url: str | None) -> LicenseFamily | None:
    if not url:
        return None
    normalized = url.strip().lower()
    return next((family for family in FAMILIES if family.pattern.search(normalized)), None)
