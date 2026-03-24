import os
import re
import time
from typing import Any, Callable, Optional

from PIL import Image


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
            {"timeout": 80},
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
        text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception:
        return ""


def canonical_category(raw: Optional[str]) -> str:
    text = normalize_label_for_match(raw or "")
    if not text:
        return ""

    rules = [
        ("sofa", ["sectional", "sofa", "couch", "loveseat", "소파"]),
        ("bed", ["bed", "침대"]),
        ("table", ["table", "desk", "console", "dining", "dining table", "테이블", "책상", "식탁", "콘솔"]),
        ("chair", ["chair", "armchair", "stool", "의자", "암체어", "스툴"]),
        ("storage", ["cabinet", "shelf", "storage", "wardrobe", "서랍", "수납", "장", "캐비닛", "선반"]),
        ("light", ["lamp", "light", "chandelier", "pendant", "sconce", "조명", "램프", "샹들리에", "스탠드"]),
        ("rug", ["rug", "carpet", "mat", "러그", "카펫", "카페트", "매트"]),
        ("tv", ["tv", "television", "티비", "텔레비전"]),
        ("mirror", ["mirror", "거울"]),
        ("plant", ["plant", "tree", "화분", "식물"]),
        ("decor", ["vase", "art", "frame", "decor", "장식", "액자", "소품"]),
    ]

    for category_name, keywords in rules:
        for keyword in keywords:
            if keyword in text:
                return category_name
    return ""


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
    category_token = safe_key_token(canonical_category(category) or category, fallback="", max_len=16)
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


def remap_match_score(src_item: dict, det_item: dict, src_idx: int, det_idx: int) -> float:
    src_label = (src_item or {}).get("label") or ""
    det_label = (det_item or {}).get("label") or ""
    base = label_match_score(src_label, det_label)

    src_cat = (src_item or {}).get("category_canonical") or canonical_category((src_item or {}).get("category") or src_label)
    det_cat = (det_item or {}).get("category_canonical") or canonical_category(det_label)

    cat_bonus = 0.0
    if src_cat and det_cat:
        if src_cat == det_cat:
            cat_bonus = 0.22
        elif base < 0.60:
            cat_bonus = -0.12

    proximity = 1.0 / (1.0 + abs(int(src_idx) - int(det_idx)))
    score = (base + cat_bonus) * 0.86 + proximity * 0.14
    return max(0.0, min(1.0, score))


def refresh_item_boxes_from_main_render(
    render_path: str,
    analyzed_items: list,
    *,
    detect_furniture_boxes: Callable[..., list],
    remap_model_name: str,
    remap_detect_timeout_sec: int,
    remap_detect_retry: int,
) -> list:
    if not isinstance(analyzed_items, list) or not analyzed_items:
        return analyzed_items
    if not render_path or not os.path.exists(render_path):
        return analyzed_items

    detected = []
    max_attempts = max(1, remap_detect_retry + 1)
    for attempt in range(max_attempts):
        try:
            raw_detected = detect_furniture_boxes(
                render_path,
                model_name=remap_model_name,
                timeout_sec=remap_detect_timeout_sec,
            )
        except Exception:
            raw_detected = []

        detected = [
            item
            for item in (raw_detected or [])
            if isinstance(item, dict) and isinstance(item.get("box_2d"), list) and len(item.get("box_2d")) == 4
        ]
        if detected:
            break
        if attempt + 1 < max_attempts:
            try:
                time.sleep(0.35 * (attempt + 1))
            except Exception:
                pass

    if not detected:
        return analyzed_items

    remaining = list(range(len(detected)))
    remapped = []

    for src_idx, src_item in enumerate(analyzed_items):
        item = dict(src_item or {})
        old_box = item.get("box_2d")
        if old_box is not None and item.get("source_box_2d") is None:
            item["source_box_2d"] = old_box

        best_idx = None
        best_score = 0.0
        for det_idx in remaining:
            det_item = detected[det_idx] if det_idx < len(detected) else {}
            score = remap_match_score(item, det_item, src_idx, det_idx)
            if score > best_score:
                best_score = score
                best_idx = det_idx

        picked_idx = None
        if best_idx is not None:
            det_best = detected[best_idx] if best_idx < len(detected) else {}
            src_cat = item.get("category_canonical") or canonical_category(item.get("category") or item.get("label") or "")
            det_cat = (det_best or {}).get("category_canonical") or canonical_category((det_best or {}).get("label") or "")
            if best_score >= 0.45 or (best_score >= 0.24 and src_cat and det_cat and src_cat == det_cat):
                picked_idx = best_idx

        if picked_idx is None and remaining:
            picked_idx = min(remaining, key=lambda value: abs(value - src_idx))

        if picked_idx is not None:
            det_item = detected[picked_idx] if picked_idx < len(detected) else {}
            det_box = det_item.get("box_2d") if isinstance(det_item, dict) else None
            if isinstance(det_box, list) and len(det_box) == 4:
                item["box_2d"] = det_box
                item["box_source"] = "main_render"
                item["box_label_detected"] = det_item.get("label")
            else:
                item["box_source"] = item.get("box_source") or "source_reference"
            if picked_idx in remaining:
                remaining.remove(picked_idx)
        else:
            item["box_source"] = item.get("box_source") or "source_reference"

        remapped.append(item)

    return remapped
