"""El registro de una persona: consentimiento, tomas con sus controles, y el paso final que
crea la identidad con un embedding por toma."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from appkit import timed
from faces.api.analysis import AnalysisPool, analyzed
from faces.api.errors import (
    CONSENT_OUTDATED,
    CONSENT_REQUIRED,
    ENROLLMENT_NOT_FOUND,
    SHOT_IN_PROGRESS,
    TOO_MANY_ATTEMPTS,
    AlreadyEnrolledError,
    ConsentOutdatedError,
    ConsentRequiredError,
    EnrollmentIncompleteError,
    EnrollmentNotFoundError,
    InvalidNameError,
    LookalikeError,
    ShotInProgressError,
    TooManyAttemptsError,
)
from faces.api.gallery import Duplicate, Gallery
from faces.api.models import (
    ConsentAcceptance,
    Enrolled,
    Enrollment,
    Person,
    ShotQuality,
    ShotResult,
    StartEnrollment,
    timestamp,
)
from faces.api.store import (
    EnrollmentGoneError,
    FaceStore,
    PendingEnrollment,
    Registration,
    StoredShot,
)
from faces.consent import ConsentText
from faces.enrollment import (
    POSE_NAMES,
    IdentityThresholds,
    InvalidDisplayNameError,
    PosePlan,
    Resemblance,
    normalized_display_name,
)
from faces.pose import Pose, pose_of
from faces.quality import QualityConfig, Rejection, RejectionReason, ShotMeasurements
from faces.recognition import Embedding, cosine_similarity
from faces.shots import AcceptedShot, RejectedShot, analyze_shot
from minigestor_client import ConflictError

Clock = Callable[[], float]
SESSION_STAGE = "sesion"
SAVE_STAGE = "guardar"


@dataclass(frozen=True, slots=True)
class EnrollmentRules:
    """Lo que configura un registro."""

    consent: ConsentText
    plan: PosePlan
    quality: QualityConfig
    thresholds: IdentityThresholds
    session_seconds: int
    retention_seconds: int
    max_attempts: int


class EnrollmentService:
    """Las operaciones del registro. No guarda nada en memoria: todo va al gestor, así que
    una sesión puede seguir en cualquier réplica."""

    def __init__(
        self,
        rules: EnrollmentRules,
        store: FaceStore,
        gallery: Gallery,
        pool: AnalysisPool,
        clock: Clock,
    ) -> None:
        self._rules = rules
        self._store = store
        self._gallery = gallery
        self._pool = pool
        self._clock = clock

    async def start(self, request: StartEnrollment) -> Enrollment:
        """Abre una sesión de registro. Sin el consentimiento vigente aceptado no se guarda
        nada.

        Raises:
            ConsentRequiredError: si falta el consentimiento o no se aceptó.
            ConsentOutdatedError: si se aceptó otra versión del texto.
            InvalidNameError: si el nombre no sirve.
        """
        self._check_consent(request.consent)
        now = self._now()
        registration = Registration(
            id=uuid.uuid4().hex,
            display_name=_display_name(request.display_name),
            consent_version=self._rules.consent.version,
            consented_at=now,
            created_at=now,
            expires_at=now + self._rules.session_seconds,
        )
        await self._store.create_enrollment(registration)
        return self._view(PendingEnrollment(registration, attempts=0), [])

    async def status(self, enrollment_id: str) -> Enrollment:
        pending = await self._pending(enrollment_id)
        return self._view(pending, await self._store.pending_shots(enrollment_id))

    async def cancel(self, enrollment_id: str) -> None:
        """Borra la sesión y sus tomas; no pasa nada si ya no estaba."""
        await self._store.discard_enrollment(enrollment_id)

    async def submit(self, enrollment_id: str, photo: bytes) -> ShotResult:
        """Analiza una toma y la guarda si pasa los controles y cubre una pose que falta.

        Raises:
            EnrollmentNotFoundError: si la sesión no existe o venció.
            TooManyAttemptsError: si ya se enviaron `max_attempts` tomas.
            AlreadyEnrolledError, LookalikeError: si el rostro ya está registrado o se
                parece demasiado a alguien; la sesión se descarta.
            ShotInProgressError: si otra toma de la sesión se guardó a la vez.
            UnreadableImageError, TooManyPixelsError: si la foto no se puede analizar.
        """
        with timed(SESSION_STAGE):
            pending, shots = await self._spend_attempt(enrollment_id)
        analysis = await analyzed(
            self._pool, partial(analyze_shot, data=photo, config=self._rules.quality)
        )
        if isinstance(analysis, RejectedShot):
            return self._rejected(pending, shots, analysis.rejection, analysis.measurements)
        pose = pose_of(analysis.measurements.head, self._rules.quality.pose)
        rejection = self._placement_rejection(pose, analysis, shots)
        if rejection is not None:
            return self._rejected(pending, shots, rejection, analysis.measurements)
        assert pose is not None
        await self._reject_duplicates(enrollment_id, [*_embeddings(shots), analysis.embedding])
        await self._keep(enrollment_id, len(shots), pose, analysis)
        stored = StoredShot(len(shots), pose, analysis.embedding, analysis.measurements)
        return self._accepted(pending, [*shots, stored], analysis)

    async def finish(self, enrollment_id: str) -> Enrolled:
        """Crea la persona con un embedding por toma, en un solo paso atómico.

        Raises:
            EnrollmentNotFoundError: si la sesión no existe, venció o ya se terminó.
            EnrollmentIncompleteError: si faltan poses del plan.
            AlreadyEnrolledError, LookalikeError: si alguien parecido se registró mientras
                tanto; la sesión se descarta.
        """
        with timed(SESSION_STAGE):
            pending = await self._pending(enrollment_id)
            shots = await self._store.pending_shots(enrollment_id)
        covered = [shot.pose for shot in shots]
        if not self._rules.plan.is_complete(covered):
            raise EnrollmentIncompleteError(self._missing_message(covered))
        await self._reject_duplicates(enrollment_id, _embeddings(shots))
        now = self._now()
        person = Registration(
            id=uuid.uuid4().hex,
            display_name=pending.registration.display_name,
            consent_version=pending.registration.consent_version,
            consented_at=pending.registration.consented_at,
            created_at=now,
            expires_at=now + self._rules.retention_seconds,
        )
        try:
            with timed(SAVE_STAGE):
                await self._store.enroll(enrollment_id, person, shots)
        except EnrollmentGoneError as error:
            raise EnrollmentNotFoundError(ENROLLMENT_NOT_FOUND) from error
        return Enrolled(
            person=person_view(person, len(shots)),
            message=(
                f"Listo, {person.display_name}: ya te reconocemos. Guarda tu código "
                f"{person.id}: con él puedes ver o borrar tu registro cuando quieras."
            ),
        )

    def _check_consent(self, acceptance: ConsentAcceptance | None) -> None:
        if acceptance is None or not acceptance.accepted:
            raise ConsentRequiredError(CONSENT_REQUIRED)
        if acceptance.version != self._rules.consent.version:
            raise ConsentOutdatedError(CONSENT_OUTDATED)

    async def _spend_attempt(
        self, enrollment_id: str
    ) -> tuple[PendingEnrollment, list[StoredShot]]:
        pending = await self._pending(enrollment_id)
        if pending.attempts >= self._rules.max_attempts:
            raise TooManyAttemptsError(TOO_MANY_ATTEMPTS)
        await self._store.count_attempt(enrollment_id)
        shots = await self._store.pending_shots(enrollment_id)
        return PendingEnrollment(pending.registration, pending.attempts + 1), shots

    async def _pending(self, enrollment_id: str) -> PendingEnrollment:
        pending = await self._store.enrollment(enrollment_id, self._now())
        if pending is None:
            raise EnrollmentNotFoundError(ENROLLMENT_NOT_FOUND)
        return pending

    def _placement_rejection(
        self, pose: Pose | None, shot: AcceptedShot, shots: Sequence[StoredShot]
    ) -> Rejection | None:
        covered = [stored.pose for stored in shots]
        placement = self._rules.plan.placement_rejection(pose, covered)
        if placement is not None:
            return placement
        least = min(
            (cosine_similarity(shot.embedding, stored.embedding) for stored in shots),
            default=None,
        )
        if least is not None and least < self._rules.thresholds.session:
            return Rejection.of(RejectionReason.DIFFERENT_PERSON)
        return None

    async def _reject_duplicates(self, enrollment_id: str, embeddings: list[Embedding]) -> None:
        duplicate = await self._gallery.duplicate_of(embeddings, self._now())
        if duplicate is None:
            return
        await self._store.discard_enrollment(enrollment_id)
        raise _duplicate_error(duplicate)

    async def _keep(self, enrollment_id: str, index: int, pose: Pose, shot: AcceptedShot) -> None:
        try:
            with timed(SAVE_STAGE):
                await self._store.add_pending_shot(enrollment_id, index, pose, shot)
        except ConflictError as error:
            raise ShotInProgressError(SHOT_IN_PROGRESS) from error

    def _missing_message(self, covered: Sequence[Pose]) -> str:
        missing = ", ".join(POSE_NAMES[pose] for pose in self._rules.plan.remaining(covered))
        return f"Faltan tomas: {missing}. {self._rules.plan.guidance(covered)}"

    def _rejected(
        self,
        pending: PendingEnrollment,
        shots: Sequence[StoredShot],
        rejection: Rejection,
        measurements: ShotMeasurements | None,
    ) -> ShotResult:
        return ShotResult(
            accepted=False,
            reason=rejection.reason,
            message=rejection.message,
            pose=None,
            quality=None if measurements is None else ShotQuality.of(measurements),
            enrollment=self._view(pending, shots),
        )

    def _accepted(
        self, pending: PendingEnrollment, shots: Sequence[StoredShot], shot: AcceptedShot
    ) -> ShotResult:
        covered = [stored.pose for stored in shots]
        return ShotResult(
            accepted=True,
            reason=None,
            message=self._rules.plan.accepted_message(covered),
            pose=covered[-1],
            quality=ShotQuality.of(shot.measurements),
            enrollment=self._view(pending, shots),
        )

    def _view(self, pending: PendingEnrollment, shots: Sequence[StoredShot]) -> Enrollment:
        covered = [shot.pose for shot in shots]
        registration = pending.registration
        return Enrollment(
            enrollment_id=registration.id,
            display_name=registration.display_name,
            poses=list(self._rules.plan.poses),
            covered=covered,
            remaining=list(self._rules.plan.remaining(covered)),
            attempts_left=max(0, self._rules.max_attempts - pending.attempts),
            expires_at=timestamp(registration.expires_at),
            message=self._rules.plan.guidance(covered),
        )

    def _now(self) -> int:
        return int(self._clock())


def person_view(person: Registration, shots: int) -> Person:
    return Person(
        person_id=person.id,
        display_name=person.display_name,
        consent_version=person.consent_version,
        consented_at=timestamp(person.consented_at),
        created_at=timestamp(person.created_at),
        expires_at=timestamp(person.expires_at),
        shots=shots,
    )


def _display_name(raw: str) -> str:
    try:
        return normalized_display_name(raw)
    except InvalidDisplayNameError as error:
        raise InvalidNameError(str(error)) from error


def _embeddings(shots: Sequence[StoredShot]) -> list[Embedding]:
    return [shot.embedding for shot in shots]


def _duplicate_error(duplicate: Duplicate) -> AlreadyEnrolledError | LookalikeError:
    if duplicate.kind is Resemblance.SAME_PERSON:
        return AlreadyEnrolledError(
            f"Ya estás registrado como «{duplicate.display_name}». Si quieres registrarte de "
            "nuevo, primero borra ese registro con su código."
        )
    return LookalikeError(
        "Te pareces mucho a alguien que ya está registrado, y el sistema podría confundirlos. "
        "Si nunca te registraste, pide ayuda a quien administra la demostración."
    )
