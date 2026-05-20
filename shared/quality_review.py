from __future__ import annotations

import json
import shutil
import textwrap
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont


REVIEW_RATINGS = ["clear_fail", "borderline", "acceptable", "strong"]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def resolve_ref_to_local_path(ref: str | None, repo_root: Path) -> Path | None:
    if not ref:
        return None
    if ref.startswith("/outputs/"):
        candidate = repo_root / "outputs" / Path(ref).name
        return candidate if candidate.exists() else None
    if ref.startswith("/assets/"):
        parts = [part for part in Path(ref).parts if part not in ("/", "\\")]
        if len(parts) >= 2:
            candidate = repo_root.joinpath(*parts)
            return candidate if candidate.exists() else None
        return None
    candidate = Path(ref)
    if candidate.exists():
        return candidate
    return None


def materialize_image_ref(ref: str | None, repo_root: Path, dest_dir: Path, stem: str) -> Path | None:
    if not ref:
        return None
    ensure_dir(dest_dir)
    local_path = resolve_ref_to_local_path(ref, repo_root)
    if local_path:
        dest = dest_dir / f"{stem}{local_path.suffix or '.png'}"
        shutil.copy2(local_path, dest)
        return dest

    parsed = urlparse(ref)
    if parsed.scheme in ("http", "https"):
        suffix = Path(parsed.path).suffix or ".png"
        dest = dest_dir / f"{stem}{suffix}"
        response = requests.get(ref, timeout=30)
        response.raise_for_status()
        dest.write_bytes(response.content)
        return dest

    return None


def build_review_sheet(*, suite_name: str, run_id: str, room_dimensions_text: str, manifest_path: str) -> dict:
    return {
        "suite_name": suite_name,
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(),
        "room_dimensions_text": room_dimensions_text,
        "manifest_path": manifest_path,
        "ratings": REVIEW_RATINGS,
        "criteria": {
            "grid_leak": {
                "rating": None,
                "notes": "",
                "rule": "Any visible scale-guide grid line or guide color in the final main render is a clear_fail.",
            },
            "detail_oversize": {
                "rating": None,
                "notes": "",
                "rule": "The target furniture in the detail shot must not read larger than the same object in the main render beyond crop-driven perspective.",
            },
            "scale_realism": {
                "rating": None,
                "notes": "",
                "rule": "Furniture must read plausibly against the fixed room dimensions and should not be miniaturized to create fake empty space.",
            },
            "placement_adherence": {
                "rating": None,
                "notes": "",
                "rule": "Requested side, anchor, and spacing intent must be visible in the final placement.",
            },
            "edit_adherence": {
                "rating": None,
                "notes": "",
                "rule": "Requested remove, replace, rearrange, or resize edits must be clearly visible in the final image.",
            },
            "generalization_risk": {
                "rating": None,
                "notes": "",
                "rule": "The run should not suggest a fix that only works for this sample category or image.",
            },
        },
    }


def collect_report_image_refs(report: dict) -> list[tuple[str, str]]:
    results = (report or {}).get("results") or {}
    refs: list[tuple[str, str]] = []

    def _add(label: str, ref: str | None) -> None:
        if ref:
            refs.append((label, ref))

    internal_main = results.get("internal_main") or {}
    _add("internal_main_original", internal_main.get("original_url"))
    _add("internal_main_empty", internal_main.get("empty_room_url"))
    _add("internal_main_scale_guide", internal_main.get("scale_guide_url"))
    _add("internal_main_result", internal_main.get("result_url"))

    internal_detail = results.get("internal_detail") or {}
    detail_urls = internal_detail.get("detail_urls") or []
    for index, ref in enumerate(detail_urls[:4], start=1):
        _add(f"internal_detail_{index}", ref)

    internal_edit = results.get("internal_image_edit") or {}
    _add("internal_edit", internal_edit.get("first_url"))

    external_cart = results.get("external_cart") or {}
    _add("external_cart_result", external_cart.get("result_url"))
    cart_detail_urls = external_cart.get("detail_urls") or []
    for index, ref in enumerate(cart_detail_urls[:2], start=1):
        _add(f"external_cart_detail_{index}", ref)

    external_preset = results.get("external_preset") or {}
    _add("external_preset_result", external_preset.get("result_url"))
    preset_detail_urls = external_preset.get("detail_urls") or []
    for index, ref in enumerate(preset_detail_urls[:2], start=1):
        _add(f"external_preset_detail_{index}", ref)

    return refs


def create_contact_sheet(items: list[tuple[str, Path]], out_path: Path, *, columns: int = 2) -> Path | None:
    valid_items = [(label, path) for label, path in items if path and path.exists()]
    if not valid_items:
        return None

    thumb_width = 520
    thumb_height = 320
    padding = 24
    label_height = 70
    rows = (len(valid_items) + columns - 1) // columns
    canvas_width = columns * thumb_width + (columns + 1) * padding
    canvas_height = rows * (thumb_height + label_height) + (rows + 1) * padding

    sheet = Image.new("RGB", (canvas_width, canvas_height), (247, 247, 247))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    for index, (label, path) in enumerate(valid_items):
        row = index // columns
        col = index % columns
        x = padding + col * thumb_width + col * padding
        y = padding + row * (thumb_height + label_height) + row * padding
        draw.rectangle((x, y, x + thumb_width, y + thumb_height + label_height), outline=(200, 200, 200), width=2)

        with Image.open(path) as image:
            preview = image.convert("RGB")
            preview.thumbnail((thumb_width - 20, thumb_height - 20))
            paste_x = x + (thumb_width - preview.width) // 2
            paste_y = y + (thumb_height - preview.height) // 2
            sheet.paste(preview, (paste_x, paste_y))

        wrapped = textwrap.wrap(label, width=40)[:3]
        text_y = y + thumb_height + 10
        for line in wrapped:
            draw.text((x + 10, text_y), line, fill=(40, 40, 40), font=font)
            text_y += 18

    ensure_dir(out_path.parent)
    sheet.save(out_path, "PNG")
    sheet.close()
    return out_path
