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
_SAME_FRAME_SCORE_THRESHOLD = float(os.getenv("DETAIL_ANGLE_QC_SAME_FRAME_THRESHOLD", "0.97") or "0.97")
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
        parallax_direction = (
            "RIGHT"
            if camera_travel_side == "LEFT"
            else "LEFT"
            if camera_travel_side == "RIGHT"
            else "the opposite screen direction"
        )
        camera_requirement = (
            f"The candidate must use a real lateral camera translation toward the {camera_travel_side} side of the "
            "source viewpoint, then yaw coherently back into the room. It must show real parallax and newly visible side planes. "
            f"Camera travel means the physical camera-body movement, not which side of the room dominates the frame. With a "
            f"{camera_travel_side} camera translation, nearby furniture should shift toward screen-{parallax_direction} relative "
            "to the far background. A crop or zoom fails."
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
        "Report the camera movement you actually observe. For inferred_camera_translation use exactly one of: "
        "left, right, up, down, forward, backward, none, unclear. Do not compare it with the requested direction yourself.\n"
        "Return STRICT JSON ONLY with every key present:\n"
        "{\n"
        '  "same_frame_or_crop": false,\n'
        '  "inferred_camera_translation": "left",\n'
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


def build_angle_camera_guide_quality_prompt(camera_mode: str, focus_side: str | None = None) -> str:
    normalized_mode = str(camera_mode or "").strip().lower()
    normalized_side = str(focus_side or "").strip().upper()
    if normalized_mode == "overview_angle":
        camera_requirement = (
            "The EMPTY ANGLE GUIDE must be a genuinely elevated camera move from the source architecture with real downward "
            "pitch, visibly increased floor or top-plane exposure, and coherent architectural projection. Inferred camera "
            "translation may be up when these overview indicators are present. A crop, zoom, or fake tilt fails."
        )
    else:
        camera_travel_side = normalized_side if normalized_side in {"LEFT", "RIGHT"} else "REQUESTED"
        camera_requirement = (
            f"The EMPTY ANGLE GUIDE must show a real lateral camera translation toward the {camera_travel_side} side of the "
            "source viewpoint, with visible lateral parallax and coherent architectural projection. Judge the physical "
            "camera-body movement; do not reject solely because the observed left/right direction differs from the requested slot."
        )

    return (
        "You are the strict V4 quality gate for an empty-room angle camera guide.\n"
        "Image #1 is SOURCE ARCHITECTURE. Image #2 is EMPTY ANGLE GUIDE.\n"
        f"{camera_requirement}\n"
        "The guide must preserve the same fixed room topology: walls, windows, doors, stairs, ceiling edges, floor boundaries, "
        "openings, built-ins, and architectural planes. Reject if architecture is added, removed, moved, mirrored, or warped. "
        "Reject if the output is only a same-frame crop, a background-only rotation, a large artificial panel/slab, or if movable "
        "furniture appears in the empty guide.\n"
        "Report the camera movement you actually observe. For inferred_camera_translation use exactly one of: "
        "left, right, up, down, forward, backward, none, unclear. Do not compare it with the requested direction yourself.\n"
        "Return STRICT JSON ONLY with every key present:\n"
        "{\n"
        '  "same_frame_or_crop": false,\n'
        '  "inferred_camera_translation": "left",\n'
        '  "room_topology_preserved": true,\n'
        '  "architecture_projection_coherent": true,\n'
        '  "background_only_rotation": false,\n'
        '  "lateral_parallax_visible": true,\n'
        '  "downward_pitch_visible": false,\n'
        '  "floor_or_top_plane_exposure_increased": false,\n'
        '  "movable_furniture_present": false,\n'
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


def _assess_angle_quality(
    source_path: str,
    candidate_path: str,
    *,
    camera_mode: str,
    focus_side: str | None = None,
    call_analysis_with_failover: Callable[..., Any] | None = None,
    analysis_model_name: str | None = None,
    safe_json_from_model_text: Callable[[str], Any] | None = None,
    require_model_qc: bool = True,
    prompt_builder: Callable[[str, str | None], str] = build_angle_quality_prompt,
    source_label: str = "SOURCE MAIN:",
    candidate_label: str = "ANGLE CANDIDATE:",
    log_tag: str = "Analysis.DetailAngleQC",
    guide_qc: bool = False,
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
                    prompt_builder(camera_mode, focus_side),
                    source_label,
                    source_for_model,
                    candidate_label,
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
                log_tag=log_tag,
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

    if guide_qc:
        required_boolean_fields = {
            "same_frame_or_crop",
            "room_topology_preserved",
            "architecture_projection_coherent",
            "background_only_rotation",
            "lateral_parallax_visible",
            "downward_pitch_visible",
            "floor_or_top_plane_exposure_increased",
            "movable_furniture_present",
            "large_artificial_panel",
            "severe_geometry_warp",
        }
    else:
        required_boolean_fields = {
            "same_frame_or_crop",
            "room_topology_preserved",
            "background_only_rotation",
            "furniture_projection_coherent",
            "large_artificial_panel",
            "severe_geometry_warp",
        }
    inferred_camera_translation = ""
    camera_direction_matches: bool | None = None
    direction_warnings: list[str] = []
    normalized_mode = str(camera_mode or "").strip().lower()
    normalized_focus_side = str(focus_side or "").strip().lower()
    allowed_camera_translations = {
        "left",
        "right",
        "up",
        "down",
        "forward",
        "backward",
        "none",
        "unclear",
    }

    if model_checked:
        if any(_coerce_bool(model_payload.get(key)) is None for key in required_boolean_fields):
            reject_reasons.append("model_qc_incomplete")
        inferred_camera_translation = str(
            model_payload.get("inferred_camera_translation") or ""
        ).strip().lower()
        if inferred_camera_translation not in allowed_camera_translations:
            reject_reasons.append("model_qc_incomplete")
        if _coerce_bool(model_payload.get("same_frame_or_crop")) is True:
            reject_reasons.append("same_frame_or_crop")
        if _coerce_bool(model_payload.get("room_topology_preserved")) is False:
            reject_reasons.append("room_topology_changed")
        if _coerce_bool(model_payload.get("background_only_rotation")) is True:
            reject_reasons.append("background_only_rotation")
        if guide_qc:
            if _coerce_bool(model_payload.get("architecture_projection_coherent")) is False:
                reject_reasons.append("architecture_projection_incoherent")
            if _coerce_bool(model_payload.get("movable_furniture_present")) is True:
                reject_reasons.append("movable_furniture_present")
        elif _coerce_bool(model_payload.get("furniture_projection_coherent")) is False:
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

        if normalized_mode == "side_angle":
            if guide_qc and _coerce_bool(model_payload.get("lateral_parallax_visible")) is not True:
                reject_reasons.append("lateral_parallax_not_visible")
            if inferred_camera_translation not in {"left", "right"}:
                reject_reasons.append("camera_translation_unclear")
            elif normalized_focus_side in {"left", "right"}:
                camera_direction_matches = inferred_camera_translation == normalized_focus_side
                if camera_direction_matches is False:
                    direction_warnings.append(
                        "direction_only_mismatch" if guide_qc else "camera_direction_mismatch"
                    )
        elif guide_qc and normalized_mode == "overview_angle":
            if _coerce_bool(model_payload.get("downward_pitch_visible")) is not True:
                reject_reasons.append("downward_pitch_not_visible")
            if _coerce_bool(model_payload.get("floor_or_top_plane_exposure_increased")) is not True:
                reject_reasons.append("floor_or_top_plane_exposure_not_increased")
            if inferred_camera_translation != "up":
                reject_reasons.append("camera_translation_unclear")
    elif require_model_qc:
        reject_reasons.append("model_qc_unavailable")

    deduped_reasons = list(dict.fromkeys(reject_reasons))
    deduped_direction_warnings = list(dict.fromkeys(direction_warnings))
    physical_qc_passed = not deduped_reasons
    requested_direction_passed = physical_qc_passed and not deduped_direction_warnings
    return {
        "passed": physical_qc_passed,
        "passed_for_requested_slot": requested_direction_passed,
        "direction_only_mismatch": physical_qc_passed and bool(deduped_direction_warnings),
        "reject_reasons": deduped_reasons,
        "warnings": deduped_direction_warnings,
        "metrics": metrics,
        "model_checked": model_checked,
        "model_payload": model_payload,
        "inferred_camera_translation": inferred_camera_translation or None,
        "camera_direction_matches": camera_direction_matches,
    }


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
    return _assess_angle_quality(
        source_path,
        candidate_path,
        camera_mode=camera_mode,
        focus_side=focus_side,
        call_analysis_with_failover=call_analysis_with_failover,
        analysis_model_name=analysis_model_name,
        safe_json_from_model_text=safe_json_from_model_text,
        require_model_qc=require_model_qc,
    )


def assess_angle_camera_guide(
    source_architecture_path: str,
    empty_guide_path: str,
    *,
    camera_mode: str,
    focus_side: str | None = None,
    call_analysis_with_failover: Callable[..., Any] | None = None,
    analysis_model_name: str | None = None,
    safe_json_from_model_text: Callable[[str], Any] | None = None,
    require_model_qc: bool = True,
) -> dict[str, Any]:
    return _assess_angle_quality(
        source_architecture_path,
        empty_guide_path,
        camera_mode=camera_mode,
        focus_side=focus_side,
        call_analysis_with_failover=call_analysis_with_failover,
        analysis_model_name=analysis_model_name,
        safe_json_from_model_text=safe_json_from_model_text,
        require_model_qc=require_model_qc,
        prompt_builder=build_angle_camera_guide_quality_prompt,
        source_label="SOURCE ARCHITECTURE:",
        candidate_label="EMPTY ANGLE GUIDE:",
        log_tag="Analysis.DetailAngleGuideQC",
        guide_qc=True,
    )
