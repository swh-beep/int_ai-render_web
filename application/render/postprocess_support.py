import os
import re
from typing import Any, Callable, Optional

from PIL import Image
from application.render.batch_detection_support import (
    build_matched_items_from_rows,
    detect_rows_from_render,
    match_items_to_detected_rows,
)


_CANONICAL_RULES = [
    ("mirror", ["mirror", "거울"]),
    ("storage", ["sideboard", "credenza", "dresser", "drawers", "cabinet", "storage", "wardrobe", "bookcase", "shelf", "수납", "서랍", "캐비닛", "선반", "장식장"]),
    ("sofa", ["lounge sofa", "lounge_sofa", "sectional", "sofa", "couch", "loveseat", "소파"]),
    ("bed", ["bed", "침대"]),
    ("table", ["side table", "coffee table", "dining table", "dining_table", "table", "desk", "console", "테이블", "책상", "콘솔"]),
    ("chair", ["lounge chair", "lounge_chair", "armchair", "chair", "stool", "pouf", "ottoman", "의자", "스툴", "푸프", "오토만"]),
    ("light", ["floor lamp", "floor_lamp", "table lamp", "table_lamp", "arc lamp", "lamp", "light", "chandelier", "pendant", "sconce", "조명", "램프"]),
    ("rug", ["rug", "carpet", "mat", "러그", "카펫", "매트"]),
    ("tv", ["tv", "television", "티비", "텔레비전"]),
    ("plant", ["plant", "tree", "화분", "식물"]),
    ("decor", ["vase", "art", "frame", "decor", "장식", "액자", "화병"]),
]

_FAMILY_KEYWORDS = {
    "mirror": ("mirror", "거울"),
    "storage": ("sideboard", "credenza", "dresser", "drawers", "cabinet", "storage", "wardrobe", "bookcase", "shelf", "수납", "서랍", "캐비닛", "선반", "장식장"),
    "stool": ("stool", "pouf", "ottoman", "footstool", "스툴", "푸프", "오토만"),
    "floor_lamp": ("floor lamp", "floor_lamp", "arc lamp"),
    "table_lamp": ("table lamp", "table_lamp", "desk lamp", "bedside lamp"),
}

_SENSITIVE_REMAP_FAMILIES = {
    "mirror",
    "storage",
    "stool",
    "floor_lamp",
    "table_lamp",
    "lounge_seating",
    "sofa",
    "table",
    "rug",
    "chair",
}


def summarize_items_for_ranking(items: list, max_items: int = 30) -> str:
    if not items:
        return ""
    lines = []
    for index, item in enumerate(items[:max_items], start=1):
        label = (item.get("label") or f"Item{index}").strip()
        qty = item.get("qty") or 1
        desc = (item.get("description") or "").strip()
        if len(desc) > 220:
            desc = desc[:220] + "..."
        qty_text = f" qty={qty}" if qty and qty > 1 else ""
        lines.append(f"{index}. {label}{qty_text}: {desc}")
    return "\n".join(lines)


def rank_best_variant_flash(
    candidate_paths: list,
    analyzed_items: list,
    *,
    call_gemini_with_failover: Callable[..., Any],
    rank_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
    timeout_sec: Optional[int] = None,
    max_attempts: Optional[int] = None,
) -> Optional[int]:
    if not candidate_paths or len(candidate_paths) < 2:
        return 0 if candidate_paths else None
    try:
        items_text = summarize_items_for_ranking(analyzed_items or [])
        prompt = (
            "You are an expert interior photo curator.\n"
            "You will receive multiple candidate images of the SAME room, labeled Candidate #1..#N.\n"
            "Select the SINGLE best candidate based on:\n"
            "1) Furniture similarity to the provided item descriptions (shape, material, color, proportions, qty).\n"
            "2) Photographic realism and aesthetic quality (lighting, coherence, natural look).\n"
            "3) Constraint compliance (no new windows/doors, no extra or missing items).\n\n"
            "ITEM LIST (REFERENCE):\n"
            f"{items_text or '(no items list)'}\n\n"
            "Return STRICT JSON ONLY:\n"
            "{\"best_index\": 1, \"reason\": \"...\"}\n"
            "best_index is 1-based."
        )
        content = [prompt]
        opened = []
        for index, path in enumerate(candidate_paths, start=1):
            try:
                image = Image.open(path)
                image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                opened.append(image)
                content.extend([f"Candidate #{index}", image])
            except Exception:
                continue

        response = call_gemini_with_failover(
            rank_model_name,
            content,
            {
                "timeout": max(10, int(timeout_sec or 80)),
                "max_attempts": max(1, int(max_attempts or 1)),
            },
            {},
            log_tag="RankBestVariant",
        )
        for image in opened:
            try:
                image.close()
            except Exception:
                pass

        parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
        if isinstance(parsed, dict):
            index = parsed.get("best_index")
            if isinstance(index, str):
                try:
                    index = int(index.strip())
                except Exception:
                    index = None
            if isinstance(index, (int, float)):
                index = int(index)
                if 1 <= index <= len(candidate_paths):
                    return index - 1
        return None
    except Exception:
        return None


def normalize_label_for_match(label: str) -> str:
    try:
        text = (label or "").strip().lower()
        text = re.sub(r"[^0-9a-z\uac00-\ud7a3+\-\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception:
        return ""


def canonical_category(raw: Optional[str]) -> str:
    text = normalize_label_for_match(raw or "")
    if not text:
        return ""
    for category_name, keywords in _CANONICAL_RULES:
        if any(keyword in text for keyword in keywords):
            return category_name
    return ""


def category_match_family(raw: Optional[str]) -> str:
    text = normalize_label_for_match(raw or "")
    canonical = canonical_category(raw)
    if not text and not canonical:
        return ""
    if any(keyword in text for keyword in _FAMILY_KEYWORDS["mirror"]):
        return "mirror"
    if any(keyword in text for keyword in _FAMILY_KEYWORDS["storage"]):
        return "storage"
    if any(keyword in text for keyword in _FAMILY_KEYWORDS["stool"]):
        return "stool"
    if "lounge" in text and any(keyword in text for keyword in ("chair", "armchair", "sofa", "sectional", "loveseat", "의자", "소파")):
        return "lounge_seating"
    if any(keyword in text for keyword in _FAMILY_KEYWORDS["floor_lamp"]):
        return "floor_lamp"
    if any(keyword in text for keyword in _FAMILY_KEYWORDS["table_lamp"]):
        return "table_lamp"
    return canonical or ""


def safe_key_token(raw: Optional[str], fallback: str = "na", max_len: int = 24) -> str:
    text = normalize_label_for_match(raw or "")
    if not text:
        return fallback
    text = text.replace(" ", "-")
    return text[:max_len] or fallback


def build_item_target_key(
    source: str,
    index: int,
    label: Optional[str] = None,
    category: Optional[str] = None,
    item_id: Optional[str] = None,
) -> str:
    src = safe_key_token(source or "item", fallback="item", max_len=12)
    idx = max(1, int(index or 1))
    item_token = safe_key_token(item_id, fallback="", max_len=24)
    category_token = safe_key_token(category_match_family(category) or canonical_category(category) or category, fallback="", max_len=16)
    label_token = safe_key_token(label, fallback="item", max_len=24)
    parts = [src]
    if item_token:
        parts.append(item_token)
    elif category_token:
        parts.append(category_token)
    parts.append(label_token)
    parts.append(f"{idx:03d}")
    return "_".join([part for part in parts if part])


def label_match_score(src_label: str, dst_label: str) -> float:
    src = normalize_label_for_match(src_label)
    dst = normalize_label_for_match(dst_label)
    if not src or not dst:
        return 0.0
    if src == dst:
        return 1.0
    score = 0.0
    if src in dst or dst in src:
        score = 0.92
    src_tokens = {token for token in src.split(" ") if token}
    dst_tokens = {token for token in dst.split(" ") if token}
    if src_tokens and dst_tokens:
        inter = len(src_tokens & dst_tokens)
        union = len(src_tokens | dst_tokens)
        jaccard = (inter / union) if union else 0.0
        score = max(score, jaccard)
    return score


def _bbox_width_height(box_2d: Any) -> tuple[float, float]:
    if not isinstance(box_2d, list) or len(box_2d) != 4:
        return 0.0, 0.0
    try:
        width = max(0.0, float(box_2d[2]) - float(box_2d[0]))
        height = max(0.0, float(box_2d[3]) - float(box_2d[1]))
        return width, height
    except Exception:
        return 0.0, 0.0


def _expected_front_aspect(src_item: dict, src_family: str) -> float | None:
    dims = (src_item or {}).get("requested_dims_mm") or (src_item or {}).get("dims_mm") or {}
    try:
        width_mm = float(dims.get("width_mm") or 0)
        depth_mm = float(dims.get("depth_mm") or 0)
        height_mm = float(dims.get("height_mm") or 0)
    except Exception:
        return None
    if width_mm <= 0:
        return None
    if src_family == "rug" and depth_mm > 0:
        return width_mm / depth_mm
    if height_mm > 0:
        return width_mm / height_mm
    return None


def _observed_aspect(det_item: dict) -> float | None:
    width, height = _bbox_width_height((det_item or {}).get("box_2d"))
    if width <= 0 or height <= 0:
        return None
    return width / height


def _aspect_match_score(src_item: dict, det_item: dict, src_family: str) -> float:
    expected = _expected_front_aspect(src_item, src_family)
    observed = _observed_aspect(det_item)
    if expected is None or observed is None:
        return 0.0
    delta = abs(expected - observed) / max(expected, observed, 1e-6)
    if delta <= 0.20:
        return 0.18
    if delta <= 0.35:
        return 0.10
    if delta >= 0.80:
        return -0.18
    return 0.0


def remap_match_score(src_item: dict, det_item: dict, src_idx: int, det_idx: int) -> float:
    src_label = (src_item or {}).get("label") or ""
    det_label = (det_item or {}).get("label") or ""
    base = label_match_score(src_label, det_label)

    src_target = str((src_item or {}).get("target_key") or "")
    det_target = str((det_item or {}).get("target_key") or "")
    if src_target and det_target and src_target == det_target:
        return 1.0

    src_source_index = str((src_item or {}).get("source_index") or "")
    det_source_index = str((det_item or {}).get("source_index") or "")
    identity_bonus = 0.16 if src_source_index and det_source_index and src_source_index == det_source_index else 0.0

    src_cat = (src_item or {}).get("category_canonical") or canonical_category((src_item or {}).get("category") or src_label)
    det_cat = (det_item or {}).get("category_canonical") or canonical_category((det_item or {}).get("category") or det_label)
    src_family = category_match_family((src_item or {}).get("category") or src_label)
    det_family = category_match_family((det_item or {}).get("category") or det_label)

    cat_bonus = 0.0
    if src_cat and det_cat:
        if src_cat == det_cat:
            cat_bonus = 0.22
        elif base < 0.60:
            cat_bonus = -0.18

    family_bonus = 0.0
    if src_family and det_family:
        if src_family == det_family:
            family_bonus = 0.24
        elif base < 0.75:
            family_bonus = -0.22

    aspect_bonus = _aspect_match_score(src_item, det_item, src_family)
    proximity = 1.0 / (1.0 + abs(int(src_idx) - int(det_idx)))
    score = (base + cat_bonus + family_bonus + identity_bonus + aspect_bonus) * 0.82 + proximity * 0.18
    return max(0.0, min(1.0, score))


def refresh_item_boxes_from_main_render(
    render_path: str,
    analyzed_items: list,
    *,
    detect_furniture_boxes: Callable[..., list],
    remap_model_name: str,
    remap_detect_timeout_sec: int,
    remap_detect_retry: int,
    remap_detect_max_attempts: int | None = None,
) -> list:
    if not isinstance(analyzed_items, list) or not analyzed_items:
        return analyzed_items
    if not render_path or not os.path.exists(render_path):
        return analyzed_items
    detected_rows = detect_rows_from_render(
        render_path,
        detect_furniture_boxes=detect_furniture_boxes,
        model_name=remap_model_name,
        timeout_sec=remap_detect_timeout_sec,
        retry=remap_detect_retry,
        max_attempts=remap_detect_max_attempts,
        canonical_category=canonical_category,
        category_match_family=category_match_family,
    )
    if not detected_rows:
        return analyzed_items

    matches = match_items_to_detected_rows(
        analyzed_items,
        detected_rows,
        remap_match_score=remap_match_score,
        category_match_family=category_match_family,
        canonical_category=canonical_category,
        sensitive_remap_families=_SENSITIVE_REMAP_FAMILIES,
    )
    return build_matched_items_from_rows(analyzed_items, matches)
