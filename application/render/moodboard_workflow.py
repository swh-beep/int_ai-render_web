import os
import shutil
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from PIL import Image


MOODBOARD_SYSTEM_PROMPT = """
ACT AS: An Expert Image Retoucher and Cataloguer.
TASK: Create a "Furniture Inventory Mood Board" by cropping and arranging the ACTUAL furniture from the input photos.

<CRITICAL INSTRUCTION: NO HALLUCINATION>
1. **DO NOT RE-DRAW OR RE-RENDER THE FURNITURE.**
2. **DO NOT CHANGE THE DESIGN.** (If the sofa has round legs, keep them round. If the rug has a specific pattern, keep it EXACTLY.)
3. Your goal is to **EXTRACT** the furniture visual data from the input images and place them on a white background.
4. If you cannot replicate the exact item, crop the best view of it from the source image.

**[STEP 1: SOURCE IDENTIFICATION]**
* Scan all provided images (Main view + Details).
* Find the clearest, best angle for each unique furniture item (Sofa, Chair, Table, Lamp, Rug, etc.).
* Ignore duplicate views. Select the one "Best Shot" for each item.

**[STEP 2: COMPOSITION RULES]**
* **Background:** Pure White (#FFFFFF).
* **Fidelity:** The items in the mood board MUST look identical to the items in the photos. Same color, same texture, same shape.
* **Layout:** Organize them in a grid.
* **Rug:** Show the rug pattern clearly as a flat swatch or perspective view from the photo.

**[STEP 3: TEXT SPECIFICATION]**
Write the specifications under each item in this strict vertical format:

Item Name x Quantity in room EA
Width: Estimated Value mm
Depth: Estimated Value mm
Height: Estimated Value mm

**Exclusion:**
- Do not include walls, ceilings, doors, or windows.
- Focus ONLY on the moveable furniture and key decor (pendant lights, floor lamps).
- OUTPUT RULE: Return a high-quality, 16:9 ratio / 2048x1152pixelimage, .
"""


def generate_moodboard_image(
    image_path: str,
    unique_id: str,
    index: int,
    furniture_specs: Optional[str] = None,
    *,
    build_prompt: Callable[[str, Optional[str]], str],
    allow_all_safety_settings: Callable[[], object],
    call_gemini_with_failover: Callable[..., object],
    model_name: str,
) -> Optional[str]:
    img = None
    try:
        img = Image.open(image_path)
        final_prompt = build_prompt(MOODBOARD_SYSTEM_PROMPT, furniture_specs)
        safety_settings = allow_all_safety_settings()
        response = call_gemini_with_failover(
            model_name,
            [final_prompt, img],
            {"timeout": 45},
            safety_settings,
            log_tag="Moodboard.Generate",
        )

        if response and hasattr(response, "candidates") and response.candidates:
            for part in response.parts:
                if hasattr(part, "inline_data"):
                    timestamp = int(time.time())
                    filename = f"gen_mb_{timestamp}_{unique_id}_{index}.png"
                    path = os.path.join("outputs", filename)
                    with open(path, "wb") as file_obj:
                        file_obj.write(part.inline_data.data)
                    return path
        return None
    except Exception as exc:
        print(f"!! Moodboard Gen Error: {exc}", flush=True)
        return None
    finally:
        try:
            if img:
                img.close()
        except Exception:
            pass


def run_generate_moodboard_options(
    file_obj,
    audience: str,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[str, str, str | None], str],
    resolve_image_url: Callable[[str | None, str | None], Optional[str]],
    log_section: Callable[[str], None],
    detect_furniture_boxes: Callable[[str], list],
    build_prompt: Callable[[str, Optional[str]], str],
    allow_all_safety_settings: Callable[[], object],
    call_gemini_with_failover: Callable[..., object],
    model_name: str,
) -> dict:
    try:
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        safe_name = "".join([c for c in file_obj.filename if c.isalnum() or c in "._-"])
        raw_path = os.path.join("outputs", f"ref_room_{timestamp}_{unique_id}_{safe_name}")

        with open(raw_path, "wb") as buffer:
            shutil.copyfileobj(file_obj.file, buffer)

        aud = normalize_audience(audience)
        prefix_customize = build_s3_prefix(aud, "customize")
        resolve_image_url(raw_path, s3_prefix_override=prefix_customize)

        log_section(f"[Moodboard Gen] Starting 3 variations for {unique_id}")

        furniture_specs_text = None
        try:
            print(">> [Moodboard Gen] Analyzing input photo context...", flush=True)
            detected = detect_furniture_boxes(raw_path)
            specs_list = [f"- {item['label']}" for item in detected]
            furniture_specs_text = "\n".join(specs_list)
        except Exception:
            print("!! [Moodboard Gen] Context analysis failed (skipping)", flush=True)

        generated_results = []
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [
                executor.submit(
                    generate_moodboard_image,
                    raw_path,
                    unique_id,
                    i + 1,
                    furniture_specs_text,
                    build_prompt=build_prompt,
                    allow_all_safety_settings=allow_all_safety_settings,
                    call_gemini_with_failover=call_gemini_with_failover,
                    model_name=model_name,
                )
                for i in range(3)
            ]
            for future in futures:
                result = future.result()
                if not result:
                    continue
                url = resolve_image_url(result, s3_prefix_override=prefix_customize)
                if url:
                    generated_results.append(url)

        if not generated_results:
            return {"error": "Failed to generate moodboards"}

        return {
            "moodboards": generated_results,
            "message": "Moodboards generated successfully",
        }
    except Exception as exc:
        print(f"[Moodboard Gen Error] {exc}", flush=True)
        traceback.print_exc()
        return {"error": str(exc)}
