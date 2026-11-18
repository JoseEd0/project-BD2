"""El pipeline con los modelos reales sobre fotos de verdad: cuántos rostros encuentra,
dónde, y qué tan parecidos salen sus embeddings."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from faces_support import (
    GROUP_PHOTO,
    GROUP_SIZE,
    OTHER_PORTRAIT,
    SINGLE_PORTRAIT,
    encoded,
    solid,
)
from PIL import Image

from faces import (
    EMBEDDING_DIMENSION,
    FacePipeline,
    ImageAnalysis,
    MultipleFacesError,
    NoFaceError,
    cosine_similarity,
)

# El umbral de coseno que publica OpenCV Zoo para SFace (LFW, 99,40 % de exactitud): por
# encima, la misma persona. El del servicio se calibra aparte, con LFW.
SFACE_SAME_PERSON = 0.363
# Una misma foto procesada de otro modo (girada, en gris, con otra escala) debe quedar
# mucho más cerca que dos fotos distintas de la misma persona.
SAME_PHOTO = 0.9
NOISE_SEED = 89
EXIF_ORIENTATION_TAG = 0x0112
ROTATE_CLOCKWISE = 6
TINY_SIDE = 96
HUGE_SIDE = 6000
FACE_CENTRE_TOLERANCE = 0.05


def resized_to_side(image: Image.Image, side: int) -> Image.Image:
    scale = side / max(image.size)
    return image.resize((round(image.width * scale), round(image.height * scale)))


def face_centre(analysis: ImageAnalysis) -> tuple[float, float]:
    box = analysis.only_face().face.box
    return (
        (box.x + box.width / 2) / analysis.width,
        (box.y + box.height / 2) / analysis.height,
    )


@pytest.fixture
def portrait_analysis(pipeline: FacePipeline, photo: Callable[[str], bytes]) -> ImageAnalysis:
    return pipeline.analyze(photo(SINGLE_PORTRAIT))


def test_a_portrait_has_exactly_one_face(portrait_analysis: ImageAnalysis):
    assert len(portrait_analysis.faces) == 1
    assert portrait_analysis.only_face().face.score > 0.9


def test_the_embedding_is_128_float32_of_norm_one(portrait_analysis: ImageAnalysis):
    embedding = portrait_analysis.only_face().embedding

    assert embedding.shape == (EMBEDDING_DIMENSION,)
    assert embedding.dtype == np.float32
    assert float(np.linalg.norm(embedding)) == pytest.approx(1.0, abs=1e-6)


def test_an_image_without_faces_gives_none(pipeline: FacePipeline):
    noise = np.random.default_rng(NOISE_SEED).integers(0, 256, (480, 640, 3), dtype=np.uint8)

    for image in (solid(640, 480, 128), Image.fromarray(noise)):
        analysis = pipeline.analyze(encoded(image, "PNG"))
        assert analysis.faces == ()
        with pytest.raises(NoFaceError):
            analysis.only_face()


def test_a_group_photo_has_every_face_from_largest_to_smallest(
    pipeline: FacePipeline, photo: Callable[[str], bytes]
):
    analysis = pipeline.analyze(photo(GROUP_PHOTO))
    areas = [found.face.box.area for found in analysis.faces]

    assert len(analysis.faces) == GROUP_SIZE
    assert areas == sorted(areas, reverse=True)
    with pytest.raises(MultipleFacesError):
        analysis.only_face()


def test_the_same_person_in_two_photos_is_above_the_sface_threshold(
    pipeline: FacePipeline, photo: Callable[[str], bytes], portrait_analysis: ImageAnalysis
):
    other = pipeline.analyze(photo(OTHER_PORTRAIT)).only_face()

    similarity = cosine_similarity(portrait_analysis.only_face().embedding, other.embedding)

    assert similarity > 2 * SFACE_SAME_PERSON


def test_the_same_person_is_found_in_the_group_and_no_one_else_matches(
    pipeline: FacePipeline, photo: Callable[[str], bytes], portrait_analysis: ImageAnalysis
):
    reference = portrait_analysis.only_face().embedding
    crew = pipeline.analyze(photo(GROUP_PHOTO)).faces

    similarities = sorted(cosine_similarity(reference, found.embedding) for found in crew)

    assert similarities[-1] > 2 * SFACE_SAME_PERSON
    assert all(similarity < SFACE_SAME_PERSON for similarity in similarities[:-1])


def test_a_photo_rotated_by_exif_gives_the_same_face(
    pipeline: FacePipeline, portrait: Image.Image, portrait_analysis: ImageAnalysis
):
    exif = Image.Exif()
    exif[EXIF_ORIENTATION_TAG] = ROTATE_CLOCKWISE
    lying_down = portrait.rotate(90, expand=True)

    analysis = pipeline.analyze(encoded(lying_down, "JPEG", exif=exif, quality=95))

    assert (analysis.width, analysis.height) == portrait.size
    similarity = cosine_similarity(
        analysis.only_face().embedding, portrait_analysis.only_face().embedding
    )
    assert similarity > SAME_PHOTO


def test_a_grayscale_photo_still_gives_the_face(
    pipeline: FacePipeline, portrait: Image.Image, portrait_analysis: ImageAnalysis
):
    analysis = pipeline.analyze(encoded(portrait.convert("L"), "PNG"))

    similarity = cosine_similarity(
        analysis.only_face().embedding, portrait_analysis.only_face().embedding
    )
    assert similarity > SAME_PHOTO


def test_a_photo_with_an_alpha_channel_still_gives_the_face(
    pipeline: FacePipeline, portrait: Image.Image, portrait_analysis: ImageAnalysis
):
    analysis = pipeline.analyze(encoded(portrait.convert("RGBA"), "PNG"))

    similarity = cosine_similarity(
        analysis.only_face().embedding, portrait_analysis.only_face().embedding
    )
    assert similarity > SAME_PHOTO


def test_a_very_large_photo_is_scaled_for_detection_with_coordinates_in_its_own_pixels(
    pipeline: FacePipeline, portrait: Image.Image, portrait_analysis: ImageAnalysis
):
    analysis = pipeline.analyze(encoded(resized_to_side(portrait, HUGE_SIDE), "JPEG"))

    assert max(analysis.width, analysis.height) == HUGE_SIDE
    assert face_centre(analysis) == pytest.approx(
        face_centre(portrait_analysis), abs=FACE_CENTRE_TOLERANCE
    )
    similarity = cosine_similarity(
        analysis.only_face().embedding, portrait_analysis.only_face().embedding
    )
    assert similarity > SAME_PHOTO


def test_a_very_small_photo_is_enlarged_until_the_face_is_found(
    pipeline: FacePipeline, portrait: Image.Image, portrait_analysis: ImageAnalysis
):
    analysis = pipeline.analyze(encoded(resized_to_side(portrait, TINY_SIDE), "PNG"))

    assert max(analysis.width, analysis.height) == TINY_SIDE
    assert face_centre(analysis) == pytest.approx(
        face_centre(portrait_analysis), abs=FACE_CENTRE_TOLERANCE
    )
    similarity = cosine_similarity(
        analysis.only_face().embedding, portrait_analysis.only_face().embedding
    )
    assert similarity > SFACE_SAME_PERSON
