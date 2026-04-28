from pathlib import Path

from PIL import Image

from application.render.furnished_generation_stage import _normalize_render_candidate_aspect
from shared.image_canvas import match_aspect_to_target


def _save_test_image(path: Path, size: tuple[int, int]) -> None:
    image = Image.new("RGB", size, color="white")
    try:
        image.save(path, "PNG")
    finally:
        image.close()


def test_normalize_render_candidate_aspect_crops_to_target_ratio(tmp_path):
    candidate_path = tmp_path / "candidate.png"
    room_path = tmp_path / "room.png"
    _save_test_image(candidate_path, (1536, 1024))
    _save_test_image(room_path, (1920, 1080))

    normalized_path = _normalize_render_candidate_aspect(
        str(candidate_path),
        str(room_path),
        expected_ratio=1920 / 1080,
        ratio_tol=0.02,
        match_aspect_to_target=match_aspect_to_target,
        log_brief=True,
    )

    assert normalized_path is not None
    with Image.open(normalized_path) as normalized:
        assert abs((normalized.size[0] / normalized.size[1]) - (1920 / 1080)) < 0.02


def test_normalize_render_candidate_aspect_keeps_valid_ratio(tmp_path):
    candidate_path = tmp_path / "candidate.png"
    room_path = tmp_path / "room.png"
    _save_test_image(candidate_path, (1600, 900))
    _save_test_image(room_path, (1920, 1080))

    normalized_path = _normalize_render_candidate_aspect(
        str(candidate_path),
        str(room_path),
        expected_ratio=1920 / 1080,
        ratio_tol=0.02,
        match_aspect_to_target=match_aspect_to_target,
        log_brief=True,
    )

    assert normalized_path == str(candidate_path)


def test_normalize_render_candidate_aspect_rejects_excessive_crop(tmp_path):
    candidate_path = tmp_path / "candidate.png"
    room_path = tmp_path / "room.png"
    _save_test_image(candidate_path, (1024, 1536))
    _save_test_image(room_path, (1920, 1080))

    normalized_path = _normalize_render_candidate_aspect(
        str(candidate_path),
        str(room_path),
        expected_ratio=1920 / 1080,
        ratio_tol=0.02,
        match_aspect_to_target=match_aspect_to_target,
        log_brief=True,
    )

    assert normalized_path is None
