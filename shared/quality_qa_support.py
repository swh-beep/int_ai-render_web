import hashlib
import json
import os
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests
from PIL import Image, ImageDraw, ImageOps


REVIEW_CRITERIA = {
    "grid_leak": "Visible scale-guide grid or guide color in final main render",
    "detail_oversize": "Detail target appears oversized versus the main render",
    "scale_realism": "Furniture scale feels realistic versus room dimensions",
    "placement_adherence": "Placement request is reflected in the generated layout",
    "edit_adherence": "Requested edit change is clearly visible in the result",
}

REVIEW_RATINGS = ["clear_fail", "borderline", "acceptable", "strong"]


@dataclass(frozen=True)
class BoardTile:
    label: str
    source: str


def slugify_token(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "item"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict | list) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def local_path_from_reference(reference: str | None, repo_root: Path) -> Optional[Path]:
    if not reference:
        return None
    text = str(reference).strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return None
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    if text.startswith("/outputs/") or text.startswith("/assets/"):
        candidate = repo_root / text.lstrip("/")
        return candidate if candidate.exists() else None
    candidate = repo_root / text
    return candidate if candidate.exists() else None


def materialize_image_reference(
    reference: str | None,
    *,
    repo_root: Path,
    cache_dir: Path,
    timeout_sec: int = 30,
) -> Optional[Path]:
    local = local_path_from_reference(reference, repo_root)
    if local is not None:
        return local
    if not reference:
        return None
    text = str(reference).strip()
    if not (text.startswith("http://") or text.startswith("https://")):
        return None

    ensure_dir(cache_dir)
    base_path = text.split("?", 1)[0]
    suffix = Path(base_path).suffix or ".bin"
    url_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    stem_token = slugify_token(Path(base_path).stem)[:48] or "item"
    filename = f"{stem_token}-{url_hash}{suffix}"
    target = cache_dir / filename
    if target.exists() and target.stat().st_size > 0:
        return target

    response = requests.get(text, timeout=timeout_sec)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target


def copy_image_reference(
    reference: str | None,
    *,
    repo_root: Path,
    cache_dir: Path,
    output_dir: Path,
    output_name: str,
) -> Optional[Path]:
    source = materialize_image_reference(reference, repo_root=repo_root, cache_dir=cache_dir)
    if source is None or not source.exists():
        return None
    ensure_dir(output_dir)
    suffix = source.suffix or ".bin"
    target = output_dir / f"{output_name}{suffix}"
    shutil.copyfile(source, target)
    return target


def build_review_sheet(
    *,
    run_id: str,
    case_id: str,
    repeat_index: int,
    room_dimensions_mm: str,
    diversity_tags: Iterable[str],
) -> dict:
    criteria = {
        key: {
            "description": description,
            "rating": None,
            "notes": "",
        }
        for key, description in REVIEW_CRITERIA.items()
    }
    return {
        "run_id": run_id,
        "case_id": case_id,
        "repeat_index": repeat_index,
        "room_dimensions_mm": room_dimensions_mm,
        "diversity_tags": list(diversity_tags),
        "review_ratings": list(REVIEW_RATINGS),
        "criteria": criteria,
        "summary": {
            "overall_rating": None,
            "notes": "",
            "recommended_action": "",
        },
    }


def _wrapped_label(draw: ImageDraw.ImageDraw, label: str, max_width: int) -> list[str]:
    words = [token for token in str(label or "").split() if token]
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial) <= max_width:
            current = trial
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines[:3]


def create_comparison_board(
    tiles: Iterable[BoardTile],
    *,
    repo_root: Path,
    cache_dir: Path,
    output_path: Path,
    columns: int = 2,
    cell_size: tuple[int, int] = (720, 480),
    label_height: int = 84,
    background: str = "#161616",
) -> Optional[Path]:
    resolved_tiles = []
    for tile in tiles:
        local = materialize_image_reference(tile.source, repo_root=repo_root, cache_dir=cache_dir)
        resolved_tiles.append((tile.label, local if local is not None and local.exists() else None))

    if not resolved_tiles:
        return None

    columns = max(1, int(columns))
    rows = (len(resolved_tiles) + columns - 1) // columns
    cell_w, cell_h = cell_size
    board = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_height)), background)
    draw = ImageDraw.Draw(board)

    for index, (label, local_path) in enumerate(resolved_tiles):
        col = index % columns
        row = index // columns
        x0 = col * cell_w
        y0 = row * (cell_h + label_height)
        image_y0 = y0
        label_y0 = y0 + cell_h

        if local_path is not None:
            with Image.open(local_path) as src:
                image = ImageOps.exif_transpose(src.convert("RGB"))
                image = ImageOps.contain(image, (cell_w, cell_h))
                padded = Image.new("RGB", (cell_w, cell_h), "#242424")
                offset_x = (cell_w - image.size[0]) // 2
                offset_y = (cell_h - image.size[1]) // 2
                padded.paste(image, (offset_x, offset_y))
                board.paste(padded, (x0, image_y0))
        else:
            draw.rectangle((x0, image_y0, x0 + cell_w, image_y0 + cell_h), fill="#2a1d1d", outline="#6f2f2f", width=3)
            draw.text((x0 + 16, image_y0 + 18), "MISSING ARTIFACT", fill="#ffd7d7")
            label = f"{label} [missing]"

        draw.rectangle((x0, label_y0, x0 + cell_w, label_y0 + label_height), fill="#1f1f1f")
        for line_index, line in enumerate(_wrapped_label(draw, label, cell_w - 24)):
            draw.text((x0 + 12, label_y0 + 12 + (line_index * 22)), line, fill="#f2f2f2")

    ensure_dir(output_path.parent)
    board.save(output_path, "PNG")
    board.close()
    return output_path


def build_review_markdown(review_sheet: dict) -> str:
    lines = [
        "# QA Review Sheet",
        "",
        f"- Run ID: `{review_sheet.get('run_id')}`",
        f"- Case ID: `{review_sheet.get('case_id')}`",
        f"- Repeat Index: `{review_sheet.get('repeat_index')}`",
        f"- Room Dimensions: `{review_sheet.get('room_dimensions_mm')}`",
        f"- Diversity Tags: {', '.join(review_sheet.get('diversity_tags') or [])}",
        "",
        "## Criteria",
        "",
    ]
    for key, row in (review_sheet.get("criteria") or {}).items():
        lines.extend(
            [
                f"### {key}",
                row.get("description") or "",
                "",
                f"- Rating: `{row.get('rating')}`",
                f"- Notes: {row.get('notes') or ''}",
                "",
            ]
        )
    lines.extend(
        [
            "## Summary",
            "",
            f"- Overall Rating: `{(review_sheet.get('summary') or {}).get('overall_rating')}`",
            f"- Recommended Action: {(review_sheet.get('summary') or {}).get('recommended_action') or ''}",
            "",
            textwrap.dedent(
                """
                Notes:
                - Use only `clear_fail`, `borderline`, `acceptable`, or `strong`.
                - This sheet is completed by the agent during development.
                - The user reviews only the final report and final representative outputs.
                """
            ).strip(),
            "",
        ]
    )
    return "\n".join(lines)


def crop_box_reference(
    image_path: Path,
    box_2d: list | tuple | None,
    *,
    output_path: Path,
    normalized_max: int = 1000,
    padding_ratio: float = 0.12,
) -> Optional[Path]:
    if not image_path.exists() or not isinstance(box_2d, (list, tuple)) or len(box_2d) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(value) for value in box_2d]
    except Exception:
        return None

    with Image.open(image_path) as src:
        image = ImageOps.exif_transpose(src.convert("RGB"))
        width, height = image.size
        left = int((xmin / normalized_max) * width)
        top = int((ymin / normalized_max) * height)
        right = int((xmax / normalized_max) * width)
        bottom = int((ymax / normalized_max) * height)
        left = max(0, min(width - 1, left))
        top = max(0, min(height - 1, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))

        pad_x = int((right - left) * max(0.0, padding_ratio))
        pad_y = int((bottom - top) * max(0.0, padding_ratio))
        left = max(0, left - pad_x)
        top = max(0, top - pad_y)
        right = min(width, right + pad_x)
        bottom = min(height, bottom + pad_y)

        crop = image.crop((left, top, right, bottom))
        ensure_dir(output_path.parent)
        crop.save(output_path, "PNG")
        crop.close()
    return output_path
