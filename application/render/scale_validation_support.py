import os
import uuid
from typing import Any, Callable, Optional

from PIL import Image


def detect_back_wall_span_norm(
    empty_room_path: str,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
) -> tuple:
    try:
        with Image.open(empty_room_path) as img:
            prompt = (
                "TASK: ROOM GEOMETRY MEASUREMENT.\\n"
                "In this empty room photo, find the BACK WALL usable span where main furniture would sit.\\n"
                "Return STRICT JSON ONLY: {\\\"x_left\\\":0.0, \\\"x_right\\\":1.0} using normalized [0..1].\\n"
                "Use the floor-wall boundary; ignore doors/windows if they reduce usable span. Approximate if unsure."
            )
            response = call_gemini_with_failover(
                analysis_model_name,
                [prompt, img],
                {"timeout": 70},
                {},
                log_tag="Analysis.BackWallSpan",
            )
            parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
            if isinstance(parsed, dict):
                x_left = float(parsed.get("x_left", 0.0))
                x_right = float(parsed.get("x_right", 1.0))
                x_left = max(0.0, min(1.0, x_left))
                x_right = max(0.0, min(1.0, x_right))
                if x_right - x_left >= 0.2:
                    return (x_left, x_right)
    except Exception:
        pass
    return (0.0, 1.0)


def detect_windows_present(
    room_path: str,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
) -> bool:
    try:
        with Image.open(room_path) as img:
            img.thumbnail((1024, 1024))
            prompt = (
                "TASK: WINDOW VISIBILITY CHECK.\n"
                "Answer ONLY with YES or NO.\n"
                "Question: Are any windows or glass exterior openings clearly visible in this room image?\n"
                "If uncertain, answer NO."
            )
            response = call_gemini_with_failover(
                analysis_model_name,
                [prompt, img],
                {"timeout": 60},
                {},
                log_tag="Analysis.WindowsPresent",
            )
            text = (response.text if response and hasattr(response, "text") else "").strip().lower()
            if text.startswith("yes"):
                return True
            if text.startswith("no"):
                return False
    except Exception:
        pass
    return False


def crop_ref_item_image(ref_path: str, box_2d: list, out_path: str):
    try:
        if not box_2d:
            return None
        with Image.open(ref_path) as img:
            width, height = img.size
            ymin, xmin, ymax, xmax = box_2d
            left = int(xmin / 1000 * width)
            top = int(ymin / 1000 * height)
            right = int(xmax / 1000 * width)
            bottom = int(ymax / 1000 * height)
            left = max(0, min(width - 1, left))
            right = max(left + 1, min(width, right))
            top = max(0, min(height - 1, top))
            bottom = max(top + 1, min(height, bottom))
            crop = img.crop((left, top, right, bottom))
            crop.save(out_path, "PNG")
            return out_path
    except Exception:
        return None


def detect_primary_bbox_norm(
    staged_path: str,
    ref_item_crop_path: Optional[str],
    primary_label: Optional[str],
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
):
    try:
        with Image.open(staged_path) as img:
            prompt = (
                "OBJECT LOCALIZATION TASK.\\n"
                "Find the PRIMARY ANCHOR furniture in the staged room image.\\n"
                "Return STRICT JSON ONLY: {\\\"xmin\\\":0.0,\\\"ymin\\\":0.0,\\\"xmax\\\":1.0,\\\"ymax\\\":1.0}.\\n"
                "bbox must tightly cover only that furniture. If reference crop is provided, match that object."
            )
            content = [prompt, "Staged room image:", img]
            if primary_label:
                content.insert(1, f"Primary label hint: {primary_label}")
            ref_img = None
            try:
                if ref_item_crop_path and os.path.exists(ref_item_crop_path):
                    ref_img = Image.open(ref_item_crop_path)
                    content += ["Reference item crop:", ref_img]
                response = call_gemini_with_failover(
                    analysis_model_name,
                    content,
                    {"timeout": 70},
                    {},
                    log_tag="Analysis.PrimaryBBox",
                )
            finally:
                if ref_img:
                    ref_img.close()
            parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
            if isinstance(parsed, dict):
                xmin = float(parsed.get("xmin", 0.0))
                xmax = float(parsed.get("xmax", 1.0))
                ymin = float(parsed.get("ymin", 0.0))
                ymax = float(parsed.get("ymax", 1.0))
                xmin = max(0.0, min(1.0, xmin))
                xmax = max(0.0, min(1.0, xmax))
                ymin = max(0.0, min(1.0, ymin))
                ymax = max(0.0, min(1.0, ymax))
                if xmax - xmin > 0.05 and ymax - ymin > 0.05:
                    return (xmin, ymin, xmax, ymax)
    except Exception:
        pass
    return None


def detect_item_bbox_norm(
    staged_path: str,
    ref_item_crop_path: Optional[str],
    item_label: Optional[str],
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
):
    try:
        with Image.open(staged_path) as img:
            prompt = (
                "OBJECT LOCALIZATION TASK.\n"
                "Find the specified furniture item in the staged room image.\n"
                "Return STRICT JSON ONLY: {\"xmin\":0.0,\"ymin\":0.0,\"xmax\":1.0,\"ymax\":1.0}.\n"
                "bbox must tightly cover only that furniture. If reference crop is provided, match that object."
            )
            content = [prompt, "Staged room image:", img]
            if item_label:
                content.insert(1, f"Item label hint: {item_label}")
            ref_img = None
            try:
                if ref_item_crop_path and os.path.exists(ref_item_crop_path):
                    ref_img = Image.open(ref_item_crop_path)
                    content += ["Reference item crop:", ref_img]
                response = call_gemini_with_failover(
                    analysis_model_name,
                    content,
                    {"timeout": 70},
                    {},
                    log_tag="Analysis.ItemBBox",
                )
            finally:
                if ref_img:
                    ref_img.close()
            parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
            if isinstance(parsed, dict):
                xmin = float(parsed.get("xmin", 0.0))
                xmax = float(parsed.get("xmax", 1.0))
                ymin = float(parsed.get("ymin", 0.0))
                ymax = float(parsed.get("ymax", 1.0))
                xmin = max(0.0, min(1.0, xmin))
                xmax = max(0.0, min(1.0, xmax))
                ymin = max(0.0, min(1.0, ymin))
                ymax = max(0.0, min(1.0, ymax))
                if xmax - xmin > 0.05 and ymax - ymin > 0.05:
                    return (xmin, ymin, xmax, ymax)
    except Exception:
        pass
    return None


def score_scale(bbox_norm: tuple, wall_span_norm: tuple, target_ratio: float) -> float:
    try:
        xmin, ymin, xmax, ymax = bbox_norm
        x_left, x_right = wall_span_norm if wall_span_norm else (0.0, 1.0)
        span = max(1e-6, (x_right - x_left))
        width = max(1e-6, (xmax - xmin))
        actual = width / span
        target = max(1e-6, float(target_ratio))
        error = abs(actual - target)
        tolerance = max(0.08, target * 0.20)
        score = 1.0 - min(1.0, error / tolerance)
        return float(max(0.0, min(1.0, score)))
    except Exception:
        return 0.0


def reorder_by_scale_best_pick(
    result_urls: list,
    ref_path: str,
    primary: dict,
    room_dims: dict,
    wall_span_norm: tuple,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
) -> list:
    try:
        room_w = int(room_dims.get("width_mm") or 0)
        primary_w = int((primary.get("dims_mm") or {}).get("width_mm") or 0)
        if room_w <= 0 or primary_w <= 0:
            return result_urls
        target_ratio = primary_w / room_w

        ref_crop = None
        try:
            out_crop = os.path.join("outputs", f"ref_primary_{uuid.uuid4().hex[:8]}.png")
            ref_crop = crop_ref_item_image(ref_path, primary.get("box_2d"), out_crop) if primary.get("box_2d") else None
        except Exception:
            ref_crop = None

        scored = []
        for index, url in enumerate(result_urls or []):
            local = os.path.join("outputs", os.path.basename(url))
            bbox = detect_primary_bbox_norm(
                local,
                ref_crop,
                primary.get("label"),
                call_gemini_with_failover=call_gemini_with_failover,
                analysis_model_name=analysis_model_name,
                safe_json_from_model_text=safe_json_from_model_text,
            )
            if bbox is None:
                scored.append((0.0, index, url))
                continue
            scored.append((score_scale(bbox, wall_span_norm, target_ratio), index, url))

        scored.sort(key=lambda row: (row[0], -row[1]), reverse=True)
        return [url for _, _, url in scored]
    except Exception:
        return result_urls


def validate_furnished_scale(
    staged_path: str,
    furniture_specs_json: dict,
    room_dims: dict,
    room_planes: Optional[dict],
    primary_label: Optional[str] = None,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
    log_brief: bool,
    logger,
):
    try:
        if not furniture_specs_json or not isinstance(furniture_specs_json, dict):
            return True, []
        items = furniture_specs_json.get("items") or []
        if not items:
            return True, []

        room_h = int((room_dims or {}).get("height_mm") or 0)
        wall_h_norm = None
        if room_planes:
            try:
                y_top = float(room_planes.get("y_top", 0.0))
                y_bottom = float(room_planes.get("y_bottom", 1.0))
                y_top = max(0.0, min(1.0, y_top))
                y_bottom = max(0.0, min(1.0, y_bottom))
                wall_h_norm = max(1e-6, (y_bottom - y_top))
            except Exception:
                wall_h_norm = None

        complete_items = []
        for item in items:
            if item.get("is_rug"):
                continue
            dims = item.get("dims_mm") or {}
            width = int(dims.get("width_mm") or 0)
            depth = int(dims.get("depth_mm") or 0)
            height = int(dims.get("height_mm") or 0)
            if width > 0 and depth > 0 and height > 0:
                complete_items.append(item)

        if not complete_items:
            return True, []

        if not primary_label:
            primary_label = (furniture_specs_json.get("primary") or {}).get("label")
        if not primary_label:
            primary_label = complete_items[0].get("label") or ""

        bboxes = {}
        for item in complete_items:
            label = item.get("label") or "Item"
            bbox = detect_item_bbox_norm(
                staged_path,
                item.get("crop_path"),
                label,
                call_gemini_with_failover=call_gemini_with_failover,
                analysis_model_name=analysis_model_name,
                safe_json_from_model_text=safe_json_from_model_text,
            )
            if bbox:
                bboxes[label] = bbox

        primary_bbox = bboxes.get(primary_label)
        primary_dims = None
        for item in complete_items:
            if (item.get("label") or "") == primary_label:
                primary_dims = item.get("dims_mm") or {}
                break

        if not primary_bbox or not primary_dims:
            return True, []

        primary_h_mm = float(primary_dims.get("height_mm") or 0)
        if primary_h_mm <= 0:
            return True, []

        _, ymin, _, ymax = primary_bbox
        primary_h_px = max(1e-6, (ymax - ymin))
        tol_rel = 0.10
        tol_room = 0.10
        issues = []

        for item in complete_items:
            label = item.get("label") or "Item"
            if label == primary_label:
                continue
            bbox = bboxes.get(label)
            if not bbox:
                continue
            dims = item.get("dims_mm") or {}
            height_mm = float(dims.get("height_mm") or 0)
            if height_mm <= 0:
                continue

            _, ymin, _, ymax = bbox
            h_px = max(1e-6, (ymax - ymin))
            observed_rel = h_px / primary_h_px
            expected_rel = height_mm / primary_h_mm
            if not log_brief:
                logger.info("[ScaleCheck] %s rel_obs=%.3f rel_exp=%.3f", label, observed_rel, expected_rel)
            rel_thresh = max(1.05, expected_rel * (1.0 + tol_rel))
            if observed_rel > rel_thresh:
                issues.append(f"{label} taller than expected vs primary")

            if room_h > 0 and wall_h_norm:
                observed_room = h_px / wall_h_norm
                expected_room = height_mm / room_h
                if not log_brief:
                    logger.info("[ScaleCheck] %s room_obs=%.3f room_exp=%.3f", label, observed_room, expected_room)
                room_thresh = max(1.10, expected_room * (1.0 + tol_room))
                if observed_room > room_thresh:
                    issues.append(f"{label} exceeds expected room height ratio")

        if issues:
            return False, issues
        return True, []
    except Exception:
        return True, []
