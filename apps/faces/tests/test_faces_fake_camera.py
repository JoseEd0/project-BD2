from __future__ import annotations

from pathlib import Path

from apps.faces.tests.fake_camera import (
    FRAME_HEIGHT,
    FRAME_MARKER,
    FRAME_WIDTH,
    Y4M_HEADER,
    write_fake_camera,
)

PORTRAIT = Path(__file__).parent / "fixtures" / "collins-1998.jpg"
# Un cuadro 4:2:0: el plano de luma entero y dos de croma a la mitad por lado.
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3 // 2


def test_the_video_is_one_420_frame_of_the_size_the_app_asks_for(tmp_path: Path) -> None:
    video = write_fake_camera(PORTRAIT, tmp_path / "camara" / "video.y4m").read_bytes()
    header = Y4M_HEADER.encode("ascii") + FRAME_MARKER
    assert video.startswith(header)
    assert len(video) == len(header) + FRAME_BYTES
