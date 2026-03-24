from typing import Callable

from PIL import Image


def analyze_room_structure(
    room_path,
    room_dimensions=None,
    timeout=120,
    *,
    call_gemini_with_failover: Callable[..., object],
    model_name: str,
    safe_json_from_model_text: Callable[[str], dict],
):
    room_img = None
    try:
        room_img = Image.open(room_path) if room_path else None
        try:
            if room_img:
                room_img.thumbnail((768, 768), Image.Resampling.LANCZOS)
        except Exception:
            pass

        prompt = (
            "You will receive ONE image: the EMPTY ROOM.\n\n"
            "TASK: Write a structural analysis of the room (80-100 words). "
            "Focus on architecture, wall layout, openings (windows/doors), ceiling and floor details. "
            "If windows are clearly present, set windows_present=true. If uncertain, use false.\n"
            f"ROOM DIMENSIONS (if provided): {room_dimensions or 'N/A'}\n\n"
            "Return STRICT JSON ONLY:\n"
            "{\n"
            '  "room_text": "...",\n'
            '  "windows_present": true/false\n'
            "}\n"
        )
        content = [prompt]
        if room_img:
            content.append(room_img)
        res = call_gemini_with_failover(
            model_name,
            content,
            {"timeout": timeout},
            {},
            log_tag="Analysis.RoomOnly",
        )
        obj = safe_json_from_model_text(res.text if res and hasattr(res, "text") else "")
        if isinstance(obj, dict):
            return obj
    except Exception as exc:
        print(f"!! [Room Analysis Failed] {exc}", flush=True)
    finally:
        if room_img:
            try:
                room_img.close()
            except Exception:
                pass
    return {}


def analyze_room_and_items_long(
    room_path,
    items,
    room_dimensions=None,
    timeout=150,
    *,
    call_gemini_with_failover: Callable[..., object],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], dict],
):
    room_img = None
    try:
        room_img = Image.open(room_path) if room_path else None
        try:
            if room_img:
                room_img.thumbnail((768, 768), Image.Resampling.LANCZOS)
        except Exception:
            pass
        item_lines = []
        for i, it in enumerate(items or [], start=1):
            label = it.get("label") or f"Item{i}"
            line = f"{i}. label='{label}'"
            dims = it.get("dims_mm")
            opts = it.get("options")
            if isinstance(dims, dict) and dims:
                try:
                    import json

                    line += f", provided_dims_mm={json.dumps(dims, ensure_ascii=False)}"
                except Exception:
                    pass
            if opts is not None and opts != "":
                try:
                    import json

                    line += f", options={json.dumps(opts, ensure_ascii=False)}"
                except Exception:
                    line += f", options={str(opts)}"
            item_lines.append(line)

        prompt = (
            "You will receive multiple images.\n"
            "Image #1 is the EMPTY ROOM. Images #2..N are individual furniture/props in the exact order below.\n\n"
            "ITEM ORDER:\n"
            + ("\n".join(item_lines) if item_lines else "(no items)")
            + "\n\n"
            "TASK A (ROOM): Write a structural analysis of the room (80-100 words). "
            "Focus on architecture, wall layout, openings (windows/doors), ceiling and floor details. "
            "If windows are clearly present, set windows_present=true. If uncertain, use false.\n"
            f"ROOM DIMENSIONS (if provided): {room_dimensions or 'N/A'}\n\n"
            "TASK B (ITEMS): For EACH item in order, write 50-70 words describing material, color, shape, proportions, "
            "silhouette, scale cues, and fine geometry. If exact dimensions are provided or readable, include them in "
            "dimensions_mm AND mention them in the description. Do NOT invent missing dimensions.\n\n"
            "If the text indicates quantity (e.g., 'x 2', '2 ea', '2pcs'), set quantity accordingly.\n"
            "Return STRICT JSON ONLY:\n"
            "{\n"
            '  "room_text": "...",\n'
            '  "windows_present": true/false,\n'
            '  "items": [\n'
            '    {"label":"...","description":"...","dimensions_mm":{"width":null,"depth":null,"height":null},"raw_text_found":"","quantity":1}\n'
            "  ]\n"
            "}\n"
        )

        content = [prompt]
        if room_img:
            content.append(room_img)
        for it in items or []:
            img = it.get("image") or it.get("_image")
            if img is not None:
                try:
                    img.thumbnail((768, 768), Image.Resampling.LANCZOS)
                except Exception:
                    pass
                content.append(img)

        res = call_gemini_with_failover(
            analysis_model_name,
            content,
            {"timeout": timeout},
            {},
            log_tag="Analysis.RoomAndItemsLong",
        )
        obj = safe_json_from_model_text(res.text if res and hasattr(res, "text") else "")
        if isinstance(obj, dict):
            return obj
    except Exception as exc:
        print(f"!! [Long Analysis Failed] {exc}", flush=True)
    finally:
        if room_img:
            try:
                room_img.close()
            except Exception:
                pass
        for it in items or []:
            img = it.get("image") or it.get("_image")
            if img is not None:
                try:
                    img.close()
                except Exception:
                    pass
    return {}
