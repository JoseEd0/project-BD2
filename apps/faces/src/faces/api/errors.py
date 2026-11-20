"""Los errores del API de rostros. Cada uno responde `{error, kind, request_id}` (ver
`appkit.errors`): `kind` es el nombre de la clase, que la interfaz usa para decidir qué
mostrar, y `error`, el mensaje para la persona."""

from __future__ import annotations

from http import HTTPStatus

from appkit import ResourceConflictError, ResourceNotFoundError, ServiceError


class ConsentRequiredError(ServiceError):
    status = HTTPStatus.BAD_REQUEST


class ConsentOutdatedError(ResourceConflictError):
    pass


class InvalidNameError(ServiceError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class EnrollmentNotFoundError(ResourceNotFoundError):
    pass


class EnrollmentIncompleteError(ResourceConflictError):
    pass


class AlreadyEnrolledError(ResourceConflictError):
    pass


class LookalikeError(ResourceConflictError):
    pass


class ShotInProgressError(ResourceConflictError):
    pass


class TooManyAttemptsError(ServiceError):
    status = HTTPStatus.TOO_MANY_REQUESTS


class UploadTooLargeError(ServiceError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class TooManyPixelsError(ServiceError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class UnreadableImageError(ServiceError):
    status = HTTPStatus.BAD_REQUEST


class PersonNotFoundError(ResourceNotFoundError):
    pass


CONSENT_REQUIRED = (
    "Sin tu consentimiento no guardamos nada. Lee el texto de GET /consent y acéptalo para "
    "registrarte."
)
CONSENT_OUTDATED = (
    "El texto del consentimiento cambió desde que lo leíste. Vuelve a leerlo y acéptalo."
)
ENROLLMENT_NOT_FOUND = "Esta sesión de registro no existe o venció. Empieza un registro nuevo."
SHOT_IN_PROGRESS = "Otra toma de esta sesión se está procesando. Envía las tomas de una en una."
TOO_MANY_ATTEMPTS = (
    "Se acabaron los intentos de esta sesión. Empieza un registro nuevo con más luz y la "
    "cara centrada."
)
UNREADABLE_IMAGE = (
    "No pudimos leer la imagen. Envía una foto JPEG, PNG o WebP tomada con la cámara."
)
TOO_MANY_PIXELS = "La foto tiene demasiada resolución. Envía una foto más pequeña."
PERSON_NOT_FOUND = "No hay un registro con ese código, o ya venció."


class FamousPhotoNotFoundError(ResourceNotFoundError):
    pass


FAMOUS_PHOTO_NOT_FOUND = "No hay una foto de LFW con ese número."
