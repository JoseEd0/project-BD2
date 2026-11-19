"""Registro, reconocimiento y borrado de punta a punta: el API de rostros con los modelos
reales, contra el gestor real, con las fotos de `apps/faces/tests/fixtures/`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from apps.faces.tests.faces_support import (
    GROUP_PHOTO,
    OTHER_PORTRAIT,
    SINGLE_PORTRAIT,
    crew_member,
    jpeg,
    portrait_image,
    selfie,
)
from fastapi.testclient import TestClient
from PIL import Image, ImageFilter
from tests.faces.conftest import ADMIN_KEY, FakeClock, ServiceFactory

from faces.enrollment import POSE_GUIDANCE
from faces.pose import Pose
from faces.quality import REJECTION_MESSAGES, RejectionReason

Rows = Callable[[str], list[dict[str, Any]]]
NAME = "Eileen Collins"
SECONDS_PER_DAY = 86_400
BLUR_RADIUS = 4
GROUP_ZOOM = 3


def consent_of(service: TestClient) -> dict[str, Any]:
    return {"version": service.get("/consent").json()["version"], "accepted": True}


def start(service: TestClient, name: str = NAME) -> str:
    response = service.post(
        "/enrollments", json={"display_name": name, "consent": consent_of(service)}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["enrollment_id"])


def send(service: TestClient, enrollment: str, photo: bytes) -> dict[str, Any]:
    response = service.post(
        f"/enrollments/{enrollment}/shots", files={"image": ("toma.jpg", photo, "image/jpeg")}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def identify(service: TestClient, photo: bytes) -> dict[str, Any]:
    response = service.post("/identify", files={"image": ("consulta.jpg", photo, "image/jpeg")})
    assert response.status_code == 200, response.text
    return dict(response.json())


def enroll_collins(service: TestClient) -> str:
    """Registra a Collins con dos tomas de frente, de dos retratos suyos."""
    enrollment = start(service)
    assert send(service, enrollment, jpeg(selfie(SINGLE_PORTRAIT)))["accepted"]
    assert send(service, enrollment, jpeg(selfie(OTHER_PORTRAIT)))["accepted"]
    finished = service.post(f"/enrollments/{enrollment}/finish")
    assert finished.status_code == 201, finished.text
    return str(finished.json()["person"]["person_id"])


def test_the_consent_is_served_with_its_version(service: TestClient):
    consent = service.get("/consent").json()
    assert consent["version"].startswith("1-")
    assert f"{consent['retention_days']} días" in consent["text"]


@pytest.mark.parametrize(
    "consent",
    [None, {"version": "cualquiera", "accepted": False}],
)
def test_without_consent_nothing_is_stored(
    service: TestClient, engine_rows: Rows, consent: dict[str, Any] | None
):
    body: dict[str, Any] = {"display_name": NAME}
    if consent is not None:
        body["consent"] = {**consent, "version": consent_of(service)["version"]}
    response = service.post("/enrollments", json=body)
    assert (response.status_code, response.json()["kind"]) == (400, "ConsentRequiredError")
    assert engine_rows("SELECT * FROM face_sesiones") == []


def test_an_outdated_consent_is_refused(service: TestClient, engine_rows: Rows):
    body = {"display_name": NAME, "consent": {"version": "0-viejo", "accepted": True}}
    response = service.post("/enrollments", json=body)
    assert (response.status_code, response.json()["kind"]) == (409, "ConsentOutdatedError")
    assert engine_rows("SELECT * FROM face_sesiones") == []


def test_a_name_that_cannot_be_shown_is_refused(service: TestClient):
    body = {"display_name": "   ", "consent": consent_of(service)}
    response = service.post("/enrollments", json=body)
    assert (response.status_code, response.json()["kind"]) == (422, "InvalidNameError")


def test_a_session_starts_with_the_plan_and_the_first_step(start_service: ServiceFactory):
    with start_service(enrollment_poses=(Pose.FRONT, Pose.LEFT, Pose.RIGHT)) as service:
        enrollment = service.get(f"/enrollments/{start(service, '  Ana  ')}").json()
    assert enrollment["display_name"] == "Ana"
    assert enrollment["remaining"] == ["front", "left", "right"]
    assert enrollment["message"] == POSE_GUIDANCE[Pose.FRONT]


def test_a_rejected_shot_says_what_to_fix_and_spends_an_attempt(service: TestClient):
    enrollment = start(service)
    blurred = selfie().filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    result = send(service, enrollment, jpeg(blurred))
    assert result["accepted"] is False
    assert result["reason"] == "blurry"
    assert result["message"] == REJECTION_MESSAGES[RejectionReason.BLURRY]
    assert result["quality"]["sharpness"] < 40
    group = send(service, enrollment, jpeg(portrait_image(GROUP_PHOTO)))
    assert group["reason"] == "multiple_faces"
    assert group["enrollment"]["attempts_left"] == 18
    assert group["enrollment"]["covered"] == []


def test_the_plan_guides_to_the_next_pose(start_service: ServiceFactory, engine_rows: Rows):
    with start_service(enrollment_poses=(Pose.FRONT, Pose.LEFT, Pose.RIGHT)) as service:
        enrollment = start(service)
        first = send(service, enrollment, jpeg(selfie()))
        again = send(service, enrollment, jpeg(selfie(OTHER_PORTRAIT)))
        finish = service.post(f"/enrollments/{enrollment}/finish")
    assert (first["accepted"], first["pose"]) == (True, "front")
    assert first["message"] == f"Toma de frente lista. {POSE_GUIDANCE[Pose.LEFT]}"
    assert first["enrollment"]["remaining"] == ["left", "right"]
    assert (again["accepted"], again["reason"]) == (False, "pose_already_taken")
    assert again["message"].endswith(POSE_GUIDANCE[Pose.LEFT])
    assert (finish.status_code, finish.json()["kind"]) == (409, "EnrollmentIncompleteError")
    assert "girada a tu izquierda, girada a tu derecha" in finish.json()["error"]
    assert len(engine_rows("SELECT id FROM face_tomas")) == 1


def test_every_shot_must_be_the_same_person(service: TestClient):
    enrollment = start(service)
    assert send(service, enrollment, jpeg(selfie()))["accepted"]
    other = send(service, enrollment, jpeg(crew_member("other")))
    assert (other["accepted"], other["reason"]) == (False, "different_person")


def test_enroll_identify_and_delete(service: TestClient, engine_rows: Rows):
    person_id = enroll_collins(service)

    assert engine_rows("SELECT id FROM face_sesiones") == []
    assert engine_rows("SELECT id FROM face_tomas") == []
    stored = engine_rows("SELECT persona, pose FROM face_embeddings")
    assert stored == [{"persona": person_id, "pose": "front"}] * 2
    person = service.get(f"/people/{person_id}").json()
    assert (person["display_name"], person["shots"]) == (NAME, 2)

    collins = identify(service, jpeg(crew_member("collins")))
    (face,) = collins["faces"]
    assert (face["status"], face["display_name"]) == ("known", NAME)
    stranger = identify(service, jpeg(crew_member("other")))["faces"][0]
    assert (stranger["status"], stranger["display_name"]) == ("unknown", None)
    group = portrait_image(GROUP_PHOTO)
    zoomed = group.resize((group.width * GROUP_ZOOM, group.height * GROUP_ZOOM))
    crew = identify(service, jpeg(zoomed))
    assert [face["display_name"] for face in crew["faces"] if face["status"] == "known"] == [NAME]
    assert crew["message"] == f"Reconocemos a {NAME}."

    assert service.delete(f"/people/{person_id}").status_code == 204
    assert service.delete(f"/people/{person_id}").status_code == 204
    assert engine_rows("SELECT id FROM face_embeddings") == []
    assert engine_rows("SELECT id FROM face_personas") == []
    assert identify(service, jpeg(crew_member("collins")))["faces"][0]["status"] == "unknown"
    assert service.get(f"/people/{person_id}").status_code == 404


def test_only_numbers_are_stored(service: TestClient, engine_app: Any):
    """Ninguna columna de las tablas del servicio guarda una imagen."""
    enroll_collins(service)
    tables = (
        TestClient(engine_app)
        .get("/tables", headers={"Authorization": f"Bearer {ADMIN_KEY}"})
        .json()
    )
    types = {column["type"] for table in tables for column in table["columns"]}
    assert types == {"STRING", "INT", "FLOAT", "VECTOR"}


def test_someone_already_enrolled_is_told_who_they_are(service: TestClient, engine_rows: Rows):
    enroll_collins(service)
    enrollment = start(service, "Otra Collins")
    response = service.post(
        f"/enrollments/{enrollment}/shots",
        files={"image": ("toma.jpg", jpeg(crew_member("collins")), "image/jpeg")},
    )
    assert (response.status_code, response.json()["kind"]) == (409, "AlreadyEnrolledError")
    assert f"«{NAME}»" in response.json()["error"]
    assert engine_rows("SELECT id FROM face_sesiones") == []
    assert len(engine_rows("SELECT id FROM face_personas")) == 1


def test_a_lookalike_is_not_enrolled_and_not_named(
    start_service: ServiceFactory, engine_rows: Rows
):
    with start_service(duplicate_threshold=0.99, lookalike_threshold=0.3) as service:
        enroll_collins(service)
        enrollment = start(service, "Parecida")
        response = service.post(
            f"/enrollments/{enrollment}/shots",
            files={"image": ("toma.jpg", jpeg(crew_member("collins")), "image/jpeg")},
        )
    assert (response.status_code, response.json()["kind"]) == (409, "LookalikeError")
    assert NAME not in response.json()["error"]
    assert engine_rows("SELECT id FROM face_tomas") == []


def test_a_session_finishes_once(service: TestClient, engine_rows: Rows):
    enrollment = start(service)
    send(service, enrollment, jpeg(selfie(SINGLE_PORTRAIT)))
    send(service, enrollment, jpeg(selfie(OTHER_PORTRAIT)))
    assert service.post(f"/enrollments/{enrollment}/finish").status_code == 201
    again = service.post(f"/enrollments/{enrollment}/finish")
    assert (again.status_code, again.json()["kind"]) == (404, "EnrollmentNotFoundError")
    assert len(engine_rows("SELECT id FROM face_personas")) == 1


def test_a_cancelled_session_leaves_nothing(service: TestClient, engine_rows: Rows):
    enrollment = start(service)
    send(service, enrollment, jpeg(selfie()))
    assert service.delete(f"/enrollments/{enrollment}").status_code == 204
    assert service.delete(f"/enrollments/{enrollment}").status_code == 204
    assert service.get(f"/enrollments/{enrollment}").status_code == 404
    assert engine_rows("SELECT id FROM face_tomas") == []


def test_a_session_expires(start_service: ServiceFactory, clock: FakeClock):
    with start_service(enrollment_ttl_seconds=60) as service:
        enrollment = start(service)
        clock.advance(61)
        response = service.post(
            f"/enrollments/{enrollment}/shots",
            files={"image": ("toma.jpg", jpeg(selfie()), "image/jpeg")},
        )
    assert (response.status_code, response.json()["kind"]) == (404, "EnrollmentNotFoundError")


def test_attempts_run_out(start_service: ServiceFactory):
    with start_service(max_shot_attempts=2) as service:
        enrollment = start(service)
        for _ in range(2):
            send(service, enrollment, jpeg(portrait_image(GROUP_PHOTO)))
        response = service.post(
            f"/enrollments/{enrollment}/shots",
            files={"image": ("toma.jpg", jpeg(selfie()), "image/jpeg")},
        )
    assert (response.status_code, response.json()["kind"]) == (429, "TooManyAttemptsError")


def test_registrations_expire_and_are_purged(
    start_service: ServiceFactory, clock: FakeClock, engine_rows: Rows
):
    with start_service(retention_days=1) as service:
        person_id = enroll_collins(service)
        abandoned = start(service, "Sin terminar")
        assert send(service, abandoned, jpeg(crew_member("other")))["accepted"]
        clock.advance(SECONDS_PER_DAY + 1)
        assert identify(service, jpeg(crew_member("collins")))["faces"][0]["status"] == "unknown"
        assert service.get(f"/people/{person_id}").status_code == 404
        report = service.portal.call(service.app.state.faces.people.purge_expired)  # type: ignore[attr-defined]
    assert (report.people, report.enrollments) == (1, 1)
    for table in ("face_personas", "face_embeddings", "face_sesiones", "face_tomas"):
        assert engine_rows(f"SELECT id FROM {table}") == []


@pytest.mark.parametrize(
    ("overrides", "photo", "status", "kind"),
    [
        ({"max_upload_bytes": 1000}, None, 413, "UploadTooLargeError"),
        ({"max_image_pixels": 10_000}, None, 413, "TooManyPixelsError"),
        ({}, b"no es una imagen", 400, "UnreadableImageError"),
        ({}, b"", 400, "UnreadableImageError"),
    ],
)
def test_photos_that_cannot_be_analyzed(
    start_service: ServiceFactory,
    overrides: dict[str, Any],
    photo: bytes | None,
    status: int,
    kind: str,
):
    with start_service(**overrides) as service:
        enrollment = start(service)
        content = jpeg(selfie()) if photo is None else photo
        response = service.post(
            f"/enrollments/{enrollment}/shots",
            files={"image": ("toma.jpg", content, "image/jpeg")},
        )
        query = service.post("/identify", files={"image": ("q.jpg", content, "image/jpeg")})
    assert (response.status_code, response.json()["kind"]) == (status, kind)
    assert (query.status_code, query.json()["kind"]) == (status, kind)


def test_an_identify_without_faces_says_so(service: TestClient):
    blank = Image.new("RGB", (640, 480), (128, 128, 128))
    result = identify(service, jpeg(blank))
    assert result == {"faces": [], "message": REJECTION_MESSAGES[RejectionReason.NO_FACE]}


def test_the_migrations_are_repeatable(start_service: ServiceFactory, engine_rows: Rows):
    for _ in range(2):
        with start_service() as service:
            assert service.get("/ready").status_code == 200
    assert engine_rows("SELECT id FROM face_personas") == []
