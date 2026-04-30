from pathlib import Path

from PIL import Image

from application.render.empty_room_generation_stage import _normalize_empty_room_ratio
from application.render.furnished_generation_stage import _normalize_render_candidate_aspect
from shared.image_canvas import image_matches_ratio, match_aspect_to_target


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


def test_normalize_render_candidate_aspect_uses_injected_postprocessor_when_it_matches_ratio(tmp_path):
    candidate_path = tmp_path / "candidate.png"
    room_path = tmp_path / "room.png"
    processed_path = tmp_path / "processed.png"
    _save_test_image(candidate_path, (800, 1000))
    _save_test_image(room_path, (1000, 1250))
    _save_test_image(processed_path, (1600, 900))

    calls = {"count": 0}

    def _postprocess(_candidate, _room):
        calls["count"] += 1
        return str(processed_path)

    normalized_path = _normalize_render_candidate_aspect(
        str(candidate_path),
        str(room_path),
        expected_ratio=16 / 9,
        ratio_tol=0.02,
        match_aspect_to_target=_postprocess,
        log_brief=True,
    )

    assert calls["count"] == 1
    assert normalized_path == str(processed_path)


def test_image_matches_ratio_uses_exif_orientation(tmp_path):
    image_path = tmp_path / "oriented.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (900, 1600), color="white").save(image_path, "JPEG", exif=exif)

    assert image_matches_ratio(str(image_path), 16 / 9)


def test_normalize_empty_room_ratio_uses_exif_orientation(tmp_path):
    image_path = tmp_path / "oriented.jpg"
    room_path = tmp_path / "room.png"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (900, 1600), color="white").save(image_path, "JPEG", exif=exif)
    _save_test_image(room_path, (1920, 1080))

    normalized_path = _normalize_empty_room_ratio(
        str(image_path),
        str(room_path),
        expected_ratio=16 / 9,
        match_aspect_to_target=match_aspect_to_target,
    )

    assert normalized_path == str(image_path)


def test_normalize_render_candidate_aspect_uses_exif_orientation(tmp_path):
    candidate_path = tmp_path / "oriented.jpg"
    room_path = tmp_path / "room.png"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (900, 1600), color="white").save(candidate_path, "JPEG", exif=exif)
    _save_test_image(room_path, (1920, 1080))

    normalized_path = _normalize_render_candidate_aspect(
        str(candidate_path),
        str(room_path),
        expected_ratio=16 / 9,
        ratio_tol=0.02,
        match_aspect_to_target=match_aspect_to_target,
        log_brief=True,
    )

    assert normalized_path == str(candidate_path)
