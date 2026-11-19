"""Las rutas del API de rostros. Solo traducen HTTP: la lógica está en los servicios."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile

from faces.api.enrollments import EnrollmentService
from faces.api.errors import UNREADABLE_IMAGE, UnreadableImageError, UploadTooLargeError
from faces.api.identification import IdentificationService
from faces.api.models import (
    Consent,
    Enrolled,
    Enrollment,
    Identification,
    Person,
    ShotResult,
    StartEnrollment,
)
from faces.api.people import PeopleService
from faces.consent import ConsentText

MEBIBYTE = 1 << 20
# El código de una persona es la llave para ver y borrar su registro: viaja en una cabecera y
# no en la ruta, que queda en los logs de acceso del servicio y del nginx.
PERSON_ID_HEADER = "X-Person-Id"


@dataclass(frozen=True, slots=True)
class FacesServices:
    """Lo que las rutas necesitan, armado al arrancar el servicio."""

    consent: ConsentText
    enrollments: EnrollmentService
    identification: IdentificationService
    people: PeopleService
    max_upload_bytes: int


def services_of(request: Request) -> FacesServices:
    services = request.app.state.faces
    assert isinstance(services, FacesServices)
    return services


Services = Annotated[FacesServices, Depends(services_of)]
Photo = Annotated[UploadFile, File(description="La foto: JPEG, PNG, WebP, BMP, GIF o TIFF.")]
PersonId = Annotated[
    str, Header(alias=PERSON_ID_HEADER, description="El código que se recibió al registrarse.")
]

router = APIRouter()


@router.get("/consent")
async def consent(services: Services) -> Consent:
    """El texto vigente del consentimiento y su versión, que se envía al registrarse."""
    return Consent.of(services.consent)


@router.post("/enrollments", status_code=HTTPStatus.CREATED)
async def start_enrollment(request: StartEnrollment, services: Services) -> Enrollment:
    return await services.enrollments.start(request)


@router.get("/enrollments/{enrollment_id}")
async def enrollment_status(enrollment_id: str, services: Services) -> Enrollment:
    return await services.enrollments.status(enrollment_id)


@router.delete("/enrollments/{enrollment_id}", status_code=HTTPStatus.NO_CONTENT)
async def cancel_enrollment(enrollment_id: str, services: Services) -> Response:
    await services.enrollments.cancel(enrollment_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.post("/enrollments/{enrollment_id}/shots")
async def submit_shot(enrollment_id: str, image: Photo, services: Services) -> ShotResult:
    """Una toma: `accepted` con la pose que cubrió, o el motivo y qué corregir."""
    photo = await read_photo(image, services.max_upload_bytes)
    return await services.enrollments.submit(enrollment_id, photo)


@router.post("/enrollments/{enrollment_id}/finish", status_code=HTTPStatus.CREATED)
async def finish_enrollment(enrollment_id: str, services: Services) -> Enrolled:
    return await services.enrollments.finish(enrollment_id)


@router.post("/identify")
async def identify(image: Photo, services: Services) -> Identification:
    photo = await read_photo(image, services.max_upload_bytes)
    return await services.identification.identify(photo)


@router.get("/person")
async def person(person_id: PersonId, services: Services) -> Person:
    return await services.people.person(person_id)


@router.delete("/person", status_code=HTTPStatus.NO_CONTENT)
async def forget_person(person_id: PersonId, services: Services) -> Response:
    """Borra a la persona y todos sus embeddings. Responde igual si ya no estaba."""
    await services.people.forget(person_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)


async def read_photo(upload: UploadFile, max_bytes: int) -> bytes:
    """Los bytes de la foto, sin leer más de `max_bytes` + 1.

    Raises:
        UploadTooLargeError: si pesa más de `max_bytes`.
        UnreadableImageError: si llegó vacía.
    """
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadTooLargeError(
            f"La foto pesa más de {max_bytes / MEBIBYTE:g} MiB. Envía una más liviana."
        )
    if not data:
        raise UnreadableImageError(UNREADABLE_IMAGE)
    return data
