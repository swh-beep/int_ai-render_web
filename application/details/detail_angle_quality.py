import json
import math
import os
from typing import Any, Callable

from PIL import Image, ImageFilter, ImageOps, ImageStat


_ANGLE_QC_PROBE_SIZE = (256, 144)
_ANGLE_QC_MODEL_MAX_EDGE = max(
    256,
    int(os.getenv("DETAIL_ANGLE_QC_MODEL_MAX_EDGE", "1024") or "1024"),
)
_SAME_FRAME_SCORE_THRESHOLD = float(os.getenv("DETAIL_ANGLE_QC_SAME_FRAME_THRESHOLD", "0.985") or "0.985")
_ANGLE_QC_MIN_CAMERA_MOTION_SCORE = float(os.getenv("DETAIL_ANGLE_QC_MIN_CAMERA_MOTION_SCORE", "0.55") or "0.55")
_ANGLE_QC_MIN_CONFIDENCE = float(os.getenv("DETAIL_ANGLE_QC_MIN_CONFIDENCE", "0.55") or "0.55")
_ANGLE_QC_ANALYSIS_TIMEOUT_SEC = max(
    1.0,
    float(os.getenv("DETAIL_ANGLE_QC_ANALYSIS_TIMEOUT_SEC", "90") or "90"),
)


def _normalized_correlation(first: list[float], second: list[float]) -> float:
    if not first or len(first) != len(second):
        return 0.0
    first_mean = sum(first) / len(first)
    second_mean = sum(second) / len(second)
    first_delta = [value - first_mean for value in first]
    second_delta = [value - second_mean for value in second]
    first_energy = sum(value * value for value in first_delta)
    second_energy = sum(value * value for value in second_delta)
    if first_energy <= 1e-9 or second_energy <= 1e-9:
        mean_delta = abs(first_mean - second_mean)
        return 1.0 if mean_delta <= 1.0 else max(0.0, 1.0 - (mean_delta / 255.0))
    covariance = sum(left * right for left, right in zip(first_delta, second_delta))
    return max(-1.0, min(1.0, covariance / math.sqrt(first_energy * second_energy)))


def _load_probe(path: str) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        return image.resize(_ANGLE_QC_PROBE_SIZE, Image.Resampling.LANCZOS)


def _load_model_image(path: str) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail(
            (_ANGLE_QC_MODEL_MAX_EDGE, _ANGLE_QC_MODEL_MAX_EDGE),
            Image.Resampling.LANCZOS,
        )
        return image


def _image_similarity_metrics(source: Image.Image, candidate: Image.Image) -> dict[str, float]:
    source_gray = ImageOps.autocontrast(ImageOps.grayscale(source))
    candidate_gray = ImageOps.autocontrast(ImageOps.grayscale(candidate))
    source_edges = ImageOps.autocontrast(source_gray.filter(ImageFilter.FIND_EDGES))
    candidate_edges = ImageOps.autocontrast(candidate_gray.filter(ImageFilter.FIND_EDGES))

    luminance_correlation = _normalized_correlation(
        [float(value) for value in source_gray.getdata()],
        [float(value) for value in candidate_gray.getdata()],
    )
    edge_correlation = _normalized_correlation(
        [float(value) for value in source_edges.getdata()],
        [float(value) for value in candidate_edges.getdata()],
    )
    same_frame_score = max(0.0, min(1.0, (0.35 * luminance_correlation) + (0.65 * edge_correlation)))
    return {
        "luminance_correlation": round(luminance_correlation, 6),
        "edge_correlation": round(edge_correlation, 6),
        "same_frame_score": round(same_frame_score, 6),
    }


def _region_texture_metrics(image: Image.Image, bounds: tuple[int, int, int, int]) -> tuple[float, float]:
    gray = ImageOps.grayscale(image.crop(bounds))
    standard_deviation = float(ImageStat.Stat(gray).stddev[0])
    edge_mean = float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0])
    return standard_deviation, edge_mean


def _new_uniform_side_panel_fraction(source: Image.Image, candidate: Image.Image) -> float:
    width, height = source.size
    detected_fraction = 0.0
    for fraction in (0.20, 0.25, 0.30, 0.35):
        band_width = max(1, int(width * fraction))
        for bounds in ((0, 0, band_width, height), (width - band_width, 0, width, height)):
            source_std, source_edge = _region_texture_metrics(source, bounds)
            candidate_std, candidate_edge = _region_texture_metrics(candidate, bounds)
            source_has_structure = source_std >= 10.0 or source_edge >= 9.0
            # FIND_EDGES also marks the outer canvas border, so a truly flat side
            # slab can still have a small non-zero edge mean.
            candidate_is_uniform = candidate_std <= 1.5 or (
                candidate_std <= 6.0 and candidate_edge <= 7.5
            )
            structure_collapsed = (
                candidate_std <= max(2.0, source_std * 0.35)
                and candidate_edge <= max(2.0, source_edge * 0.35)
            )
            if source_has_structure and candidate_is_uniform and structure_collapsed:
                detected_fraction = max(detected_fraction, fraction)
    return detected_fraction


def build_angle_quality_prompt(camera_mode: str, focus_side: str | None = None) -> str:
    normalized_mode = str(camera_mode or "").strip().lower()
    normalized_side = str(focus_side or "").strip().upper()
    if normalized_mode == "overview_angle":
        camera_requirement = (
            "The candidate must use a genuinely elevated camera position with a real downward pitch, visibly increased "
            "top-surface/floor exposure, and coherent perspective change. A crop, zoom, or fake height impression fails."
        )
    else:
        camera_travel_side = normalized_side if normalized_side in {"LEFT", "RIGHT"} else "REQUESTED"
        camera_requirement = (
            f"The candidate must use a real lateral camera translation toward the {camera_travel_side} side of the "
            "source viewpoint, then yaw coherently back into the room. It must show real parallax and newly visible side planes. "
            "A crop or zoom fails."
        )

    return (
        "You are the strict quality gate for an interior-design angle photograph.\n"
        "Image #1 is SOURCE MAIN. Image #2 is ANGLE CANDIDATE.\n"
        f"{camera_requirement}\n"
        "The room and furniture must be the same physical scene. Furniture world-space positions and physical orientations "
        "must stay fixed, but their screen positions, visible sides, occlusions, and perspective MUST change coherently with "
        "the camera. Reject a candidate where furniture stays front-facing like a flat sticker while only the room changes.\n"
        "Reject if any wall, window, door, stair, ceiling edge, floor boundary, or opening is added, removed, moved, mirrored, "
        "or severely warped. Reject large artificial gray/white foreground panels, wall slabs, or mask-like vertical bands.\n"
        "Ignore color temperature and white balance. Judge camera motion, physical coherence, and geometry only.\n"
        "Return STRICT JSON ONLY with every key present:\n"
        "{\n"
        '  "same_frame_or_crop": false,\n'
        '  "camera_direction_matches": true,\n'
        '  "room_topology_preserved": true,\n'
        '  "background_only_rotation": false,\n'
        '  "furniture_projection_coherent": true,\n'
        '  "large_artificial_panel": false,\n'
        '  "severe_geometry_warp": false,\n'
        '  "camera_motion_score": 0.0,\n'
        '  "confidence": 0.0,\n'
        '  "reasons": []\n'
        "}"
    )


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _coerce_score(value: Any) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return None


def _parse_model_payload(
    response: Any,
    safe_json_from_model_text: Callable[[str], Any] | None,
) -> dict[str, Any]:
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        return {}
    if safe_json_from_model_text is not None:
        parsed = safe_json_from_model_text(text)
    else:
        parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else {}


def assess_angle_candidate(
    source_path: str,
    candidate_path: str,
    *,
    camera_mode: str,
    focus_side: str | None = None,
    call_analysis_with_failover: Callable[..., Any] | None = None,
    analysis_model_name: str | None = None,
    safe_json_from_model_text: Callable[[str], Any] | None = None,
    require_model_qc: bool = True,
) -> dict[str, Any]:
    source_probe = _load_probe(source_path)
    candidate_probe = _load_probe(candidate_path)
    try:
        metrics = _image_similarity_metrics(source_probe, candidate_probe)
        uniform_panel_fraction = _new_uniform_side_panel_fraction(source_probe, candidate_probe)
    finally:
        source_probe.close()
        candidate_probe.close()
    metrics["uniform_side_panel_fraction"] = uniform_panel_fraction

    reject_reasons: list[str] = []
    if metrics["same_frame_score"] >= _SAME_FRAME_SCORE_THRESHOLD:
        reject_reasons.append("same_frame_or_crop")
    if uniform_panel_fraction > 0.0:
        reject_reasons.append("large_artificial_panel")

    model_payload: dict[str, Any] = {}
    model_checked = False
    analysis_available = call_analysis_with_failover is not None and bool(str(analysis_model_name or "").strip())
    if analysis_available:
        source_for_model = None
        candidate_for_model = None
        try:
            source_for_model = _load_model_image(source_path)
            candidate_for_model = _load_model_image(candidate_path)
            response = call_analysis_with_failover(
                analysis_model_name,
                [
                    build_angle_quality_prompt(camera_mode, focus_side),
                    "SOURCE MAIN:",
                    source_for_model,
                    "ANGLE CANDIDATE:",
                    candidate_for_model,
                ],
                {
                    "timeout": _ANGLE_QC_ANALYSIS_TIMEOUT_SEC,
                    "max_attempts": 1,
                    "temperature": 0,
                    "seed": 17,
                    "response_mime_type": "application/json",
                },
                {},
                log_tag="Analysis.DetailAngleQC",
            )
            model_payload = _parse_model_payload(response, safe_json_from_model_text)
            model_checked = bool(model_payload)
        except Exception:
            model_payload = {}
        finally:
            if source_for_model is not None:
                source_for_model.close()
            if candidate_for_model is not None:
                candidate_for_model.close()

    required_boolean_fields = {
        "same_frame_or_crop",
        "camera_direction_matches",
        "room_topology_preserved",
        "background_only_rotation",
        "furniture_projection_coherent",
        "large_artificial_panel",
        "severe_geometry_warp",
    }
    if model_checked:
        if any(_coerce_bool(model_payload.get(key)) is None for key in required_boolean_fields):
            reject_reasons.append("model_qc_incomplete")
        if _coerce_bool(model_payload.get("same_frame_or_crop")) is True:
            reject_reasons.append("same_frame_or_crop")
        if _coerce_bool(model_payload.get("camera_direction_matches")) is False:
            reject_reasons.append("camera_direction_mismatch")
        if _coerce_bool(model_payload.get("room_topology_preserved")) is False:
            reject_reasons.append("room_topology_changed")
        if _coerce_bool(model_payload.get("background_only_rotation")) is True:
            reject_reasons.append("background_only_rotation")
        if _coerce_bool(model_payload.get("furniture_projection_coherent")) is False:
            reject_reasons.append("furniture_projection_incoherent")
        if _coerce_bool(model_payload.get("large_artificial_panel")) is True:
            reject_reasons.append("large_artificial_panel")
        if _coerce_bool(model_payload.get("severe_geometry_warp")) is True:
            reject_reasons.append("severe_geometry_warp")

        camera_motion_score = _coerce_score(model_payload.get("camera_motion_score"))
        confidence = _coerce_score(model_payload.get("confidence"))
        if camera_motion_score is None or camera_motion_score < _ANGLE_QC_MIN_CAMERA_MOTION_SCORE:
            reject_reasons.append("insufficient_camera_motion")
        if confidence is None or confidence < _ANGLE_QC_MIN_CONFIDENCE:
            reject_reasons.append("model_qc_low_confidence")
        metrics["camera_motion_score"] = camera_motion_score
        metrics["model_confidence"] = confidence
    elif require_model_qc:
        reject_reasons.append("model_qc_unavailable")

    deduped_reasons = list(dict.fromkeys(reject_reasons))
    return {
        "passed": not deduped_reasons,
        "reject_reasons": deduped_reasons,
        "metrics": metrics,
        "model_checked": model_checked,
        "model_payload": model_payload,
    }
