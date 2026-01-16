# -*- coding: utf-8 -*-
import os
import time
import threading
from pathlib import Path
import subprocess
from urllib.parse import urlparse
import shutil
import base64
import uuid
import requests
import json
import google.generativeai as genai
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from styles_config import STYLES, ROOM_STYLES
from PIL import Image, ImageOps
import re
import traceback
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic import BaseModel
import gc
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------
# 1. ?섍꼍 ?ㅼ젙 諛?珥덇린??# ---------------------------------------------------------
load_dotenv()

MODEL_NAME = 'gemini-3-pro-image-preview'       # ?덈? 蹂寃?湲덉?
ANALYSIS_MODEL_NAME = 'gemini-3-flash-preview'  # ?덈? 蹂寃?湲덉?
API_KEY_POOL = []
i = 1
while True:
    key = os.getenv(f"NANOBANANA_API_KEY_{i}") 
    if not key:
        key = os.getenv(f"NANOBANANA_API_KEY{i}")
        if not key: break
    API_KEY_POOL.append(key)
    i += 1

if not API_KEY_POOL:
    single_key = os.getenv("NANOBANANA_API_KEY")
    if single_key: API_KEY_POOL.append(single_key)

print(f"??濡쒕뱶???섎끂諛붾굹??API ??媛쒖닔: {len(API_KEY_POOL)}媛?, flush=True)

MAGNIFIC_API_KEY = os.getenv("MAGNIFIC_API_KEY")
MAGNIFIC_ENDPOINT = os.getenv("MAGNIFIC_ENDPOINT", "https://api.freepik.com/v1/ai/image-upscaler")
TOTAL_TIMEOUT_LIMIT = 300 

os.makedirs("outputs", exist_ok=True)
os.makedirs("assets", exist_ok=True)
os.makedirs("static", exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

QUOTA_EXCEEDED_KEYS = set()

def call_gemini_with_failover(model_name, contents, request_options, safety_settings, system_instruction=None):
    global API_KEY_POOL, QUOTA_EXCEEDED_KEYS
    max_retries = len(API_KEY_POOL) + 5
    
    for attempt in range(max_retries):
        available_keys = [k for k in API_KEY_POOL if k not in QUOTA_EXCEEDED_KEYS]
        if not available_keys:
            print("?봽 [System] 紐⑤뱺 ?ㅺ? ???곹깭. 5珥?荑⑤떎????珥덇린??", flush=True)
            time.sleep(5) 
            QUOTA_EXCEEDED_KEYS.clear()
            available_keys = list(API_KEY_POOL)

        current_key = random.choice(available_keys)
        masked_key = current_key[-4:]

        try:
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel(model_name, system_instruction=system_instruction) if system_instruction else genai.GenerativeModel(model_name)
            
            response = model.generate_content(contents, request_options=request_options, safety_settings=safety_settings)
            return response

        except Exception as e:
            error_msg = str(e)
            if any(x in error_msg for x in ["429", "403", "Quota", "limit", "Resource has been exhausted"]):
                print(f"?뱣 [Lock] Key(...{masked_key}) ?좊떦??珥덇낵. (?좎떆 ?댁떇)", flush=True)
                QUOTA_EXCEEDED_KEYS.add(current_key)
                time.sleep(2 + attempt) 
            else:
                print(f"?좑툘 [Error] Key(...{masked_key}) ?먮윭: {error_msg}", flush=True)
                time.sleep(1)

    print("??[Fatal] 紐⑤뱺 ???쒕룄 ?ㅽ뙣.", flush=True)
    return None

def standardize_image(image_path, output_path=None, keep_ratio=False, force_landscape=False):
    try:
        if output_path is None: output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            
            # [?섏젙] ?щ챸 諛곌꼍(RGBA) 泥섎━: ?곗깋 ?뚰뭹????諛곌꼍??臾삵엳??寃껋쓣 諛⑹??섍린 ?꾪빐 以묐┰ 洹몃젅??#D2D2D2) 諛곌꼍 ?ъ슜
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
                # 諛앹? 媛援ъ? ?대몢??媛援?紐⑤몢 ?鍮꾧? ??蹂댁씠??以묐┰?곸씤 ?뚯깋 諛곌꼍 ?앹꽦
                background = Image.new("RGBA", img.size, (210, 210, 210, 255)) 
                img = Image.alpha_composite(background, img).convert("RGB")
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            width, height = img.size
            
            # [FIX] force_landscape媛 True硫?-> 臾댁“嫄?16:9 (1920x1080) ?ㅼ젙
            if force_landscape:
                target_size = (1920, 1080)
                target_ratio = 16 / 9
            # 湲곗〈 濡쒖쭅 (?먮룞 媛먯?)
            elif width >= height:
                target_size = (1920, 1080)
                target_ratio = 16 / 9
            else:
                target_size = (1080, 1350)
                target_ratio = 4 / 5

            if not keep_ratio:
                current_ratio = width / height

                if current_ratio > target_ratio:
                    # ?대?吏媛 ???⑹옉??寃쎌슦 (?묒쁿 ?먮쫫)
                    new_width = int(height * target_ratio)
                    offset = (width - new_width) // 2
                    img = img.crop((offset, 0, offset + new_width, height))
                else:
                    # ?대?吏媛 ???彛됲븳 寃쎌슦 (?꾩븘???먮쫫)
                    new_height = int(width / target_ratio)
                    offset = (height - new_height) // 2
                    img = img.crop((0, offset, width, offset + new_height))

                # 理쒖쥌 由ъ궗?댁쫰 (LANCZOS ?꾪꽣 ?ъ슜)
                img = img.resize(target_size, Image.Resampling.LANCZOS)

            base, _ = os.path.splitext(output_path)
            new_output_path = f"{base}.png"
            img.save(new_output_path, "PNG")
            return new_output_path
    except Exception as e:
        print(f"!! ?쒖????ㅽ뙣: {e}", flush=True)
        return image_path
# ---------------------------------------------------------
# [NEW] Output Aspect Ratio Enforcement
# - Gemini媛 臾대뱶蹂대뱶 鍮꾩쑉/?덉씠?꾩썐???곕씪媛嫄곕굹,
#   ?섎떒????諛곌꼍(移댄깉濡쒓렇/?띿뒪?? ?곸뿭??遺숈뿬???대낫?대뒗 耳?댁뒪瑜?#   "諛??ъ쭊 罹붾쾭?? 湲곗??쇰줈 媛뺤젣 蹂댁젙?⑸땲??
# ---------------------------------------------------------

def _is_bottom_strip_mostly_white(img: Image.Image, strip_ratio: float = 0.22, white_thresh: int = 245) -> bool:
    """?섎떒 strip??'嫄곗쓽 ?곗깋'?몄? ?대━?ㅽ떛?쇰줈 ?먮떒?⑸땲??

    - 臾대뱶蹂대뱶/?몃깽?좊━ ?쒗듃媛 ?섎떒??遺숇뒗 寃쎌슦 ??諛곌꼍??????ы븿?섎뒗 ?⑦꽩??留롮븘??      landscape 媛뺤젣 ?щ∼ ??'?꾩そ 怨좎젙(top anchor)' ?щ?瑜?寃곗젙?섎뒗 ???ъ슜?⑸땲??
    """
    try:
        w, h = img.size
        if w <= 0 or h <= 0:
            return False

        strip_h = max(1, int(h * strip_ratio))
        y0 = max(0, h - strip_h)
        strip = img.crop((0, y0, w, h))

        # 怨꾩궛 鍮꾩슜????텛湲??꾪빐 異뺤냼 ???먮떒
        strip = strip.resize((256, max(1, int(256 * strip_ratio))), Image.Resampling.BILINEAR)
        gray = strip.convert('L')
        pixels = list(gray.getdata())
        if not pixels:
            return False

        white_count = sum(1 for p in pixels if p >= white_thresh)
        white_ratio = white_count / len(pixels)

        # 35% ?댁긽???쒕갚(洹쇱쿂)?대㈃ "?섎떒?????쒗듃"???뺣쪧???믩떎怨?媛??        return white_ratio >= 0.35
    except Exception:
        return False


def standardize_image_to_reference_canvas(
    image_path: str,
    reference_path: str,
    output_path: Optional[str] = None,
) -> str:
    """?앹꽦 寃곌낵臾쇱쓣 'reference ?대?吏(=鍮?諛?罹붾쾭??'??鍮꾩쑉/?댁긽?꾨줈 媛뺤젣 ?듭씪?⑸땲??

    - ?듭떖: 臾대뱶蹂대뱶媛 ?몃줈?щ룄 理쒖쥌 寃곌낵??諛??ъ쭊 罹붾쾭??16:9 ?먮뒗 4:5)濡?媛뺤젣.
    - 異붽?: 寃곌낵 ?대?吏媛 ?몃줈濡??硫댁꽌 ?섎떒?????몃깽?좊━ ?곸뿭??遺숇뒗 耳?댁뒪瑜?            top-anchor ?щ∼?쇰줈 ?섎씪?대뒗 ?대━?ㅽ떛???곸슜.
    """
    try:
        with Image.open(reference_path) as ref_img:
            ref_img = ImageOps.exif_transpose(ref_img)
            ref_w, ref_h = ref_img.size
            if ref_w <= 0 or ref_h <= 0:
                return image_path

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            w, h = img.size
            if w <= 0 or h <= 0:
                return image_path

            target_ratio = ref_w / ref_h
            current_ratio = w / h

            # ?대? 紐⑺몴 罹붾쾭?ㅼ? ?숈씪?섎㈃ 洹몃?濡?PNG濡쒕쭔 ???(?덉쟾)
            if abs(current_ratio - target_ratio) < 1e-3 and (w, h) == (ref_w, ref_h):
                base, _ = os.path.splitext(output_path or image_path)
                out_path = f"{base}.png"
                img.save(out_path, "PNG")
                return out_path

            if current_ratio > target_ratio:
                # ?덈Т ?볦쓬: 醫뚯슦 ?щ∼
                new_w = int(h * target_ratio)
                x0 = max(0, (w - new_w) // 2)
                img = img.crop((x0, 0, x0 + new_w, h))
            else:
                # ?덈Т ?믪쓬: ?곹븯 ?щ∼
                new_h = int(w / target_ratio)
                new_h = min(new_h, h)

                # ?섎떒?????쒗듃媛 遺숇뒗 ?⑦꽩?대㈃ ?꾩そ 湲곗??쇰줈 ?щ∼ (?섎떒 ?쒓굅)
                if ref_w >= ref_h and _is_bottom_strip_mostly_white(img):
                    y0 = 0
                else:
                    y0 = max(0, (h - new_h) // 2)

                img = img.crop((0, y0, w, y0 + new_h))

            img = img.resize((ref_w, ref_h), Image.Resampling.LANCZOS)

            base, _ = os.path.splitext(output_path or image_path)
            out_path = f"{base}_fit.png"
            img.save(out_path, "PNG")
            return out_path
    except Exception as e:
        print(f"!! [Canvas Fit Failed] {e}", flush=True)
        return image_path

# -----------------------------------------------------------------------------
# [CORE] Analysis Logic (Global Definition)
# -----------------------------------------------------------------------------

def detect_furniture_boxes(moodboard_path):
    print(f">> [Detection] Scanning furniture in {moodboard_path}...", flush=True)
    try:
        img = Image.open(moodboard_path)
        prompt = (
            "OBJECT DETECTION TASK:\n"
            "Identify ALL discrete furniture items in this image (Sofa, Chair, Table, Lamp, Rug, Ottoman, etc.).\n"
            "**NOTE:** The background is a neutral grey (#D2D2D2) for contrast. Do not detect the background itself.\n"
            "Return a JSON list where each item has:\n"
            "- 'label': Name of the item.\n"
            "- 'box_2d': [ymin, xmin, ymax, xmax] coordinates normalized to 0-1000 scale.\n"
            "\n"
            "<CRITICAL: SORTING ORDER>\n"
            "**YOU MUST SORT THE LIST BY PHYSICAL SIZE (VOLUME) FROM LARGEST TO SMALLEST.**\n"
            "1. Largest items first (e.g., Sofa, Bed, Large Rug, Wardrobe).\n"
            "2. Medium items second (e.g., Armchair, Coffee Table, Console).\n"
            "3. Small items last (e.g., Side Table, Lamp, Vase, Decor).\n"
            "Ignore walls, windows, and floors. Focus on movable objects."
        )
        response = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [prompt, img], {'timeout': 60}, {})
        if response and response.text:
            text = response.text.strip()
            if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text: text = text.split("```")[0].strip()
            
            items = json.loads(text)
            if isinstance(items, list) and len(items) > 0:

                print(f">> [Detection] Found {len(items)} items (Sorted): {[i.get('label') for i in items]}", flush=True)
                return items
    except Exception as e:
        print(f"!! Detection Failed: {e}", flush=True)
    
    return [{"label": "Main Furniture"}, {"label": "Coffee Table"}, {"label": "Lounge Chair"}]

def analyze_cropped_item(moodboard_path, item_data):
    try:
        box = item_data.get('box_2d')
        label = item_data.get('label', 'Furniture')
        
        img = Image.open(moodboard_path)
        width, height = img.size
        
        if box:
            ymin, xmin, ymax, xmax = box
            left = int(xmin / 1000 * width)
            top = int(ymin / 1000 * height)
            right = int(xmax / 1000 * width)
            bottom = int(ymax / 1000 * height)
            cropped_img = img.crop((left, top, right, bottom))
        else:
            cropped_img = img

        prompt = (
            "f\"Describe the visual traits of this '{label}' for a 3D artist.\\n\"\n"
            "\"Focus ON:\\n\"\n"
            "\"1. Material (e.g., leather, wood, fabric type)\\n\"\n"
            "\"2. Color (exact shade)\\n\"\n"
            "\"3. Shape & Structure (legs, armrests, silhouette)\\n\"\n"
            "\"4. **PHYSICAL DIMENSIONS:** Read and extract any dimensions (e.g., width, depth, height in mm) written near or under the item.\\n\\n\"\n"
            
            "\"<CRITICAL: NEGATIVE CONSTRAINTS>\\n\"\n"
            "\"1. **IGNORE BACKGROUND:** The background is a neutral grey (#D2D2D2) added for contrast. Do NOT mention 'grey background'. Treat the object as if it is floating.\\n\"\n"
            "\"2. **NO LAYOUT INFO:** Do not describe it as 'collage' or 'grid'.\\n\"\n"
            "\"OUTPUT FORMAT: Include dimensions if found, followed by a concise visual description. 60-100 words.\"\n"
        )
        response = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [prompt, cropped_img], {'timeout': 30}, {})
        
        if response and response.text:
            return {"label": label, "description": response.text.strip()}
            
    except Exception as e:
        print(f"!! Crop Analysis Failed for {label}: {e}", flush=True)
    
    return {"label": label, "description": f"A high quality {label}."}

# [理쒖쥌 蹂듦뎄 諛??낃렇?덉씠?? 遺꾩꽍(Flash) -> ?앹꽦(Pro-Image) 2?④퀎 ?뚯씠?꾨씪??# 援ш? AI ?ㅽ뒠?붿삤??"Generative Reconstruction" 濡쒖쭅 ?댁떇
def generate_frontal_room_from_photos(photo_paths, unique_id, index):
    try:
        print(f"   [Frontal Gen] Step 1: Analyzing {len(photo_paths)} photos with Flash (Spatial Mapping)...", flush=True)
        
        # 1. ?대?吏 濡쒕뱶
        input_images = []
        for path in photo_paths:
            try:
                img = Image.open(path)
                img.thumbnail((1536, 1536))
                input_images.append(img)
            except: pass

        if not input_images:
            return None

        # ---------------------------------------------------------
        # [Step 1] Flash 紐⑤뜽濡?"怨듦컙 援ъ“ 諛?3D 留ㅽ븨" 遺꾩꽍
        # AI ?ㅽ뒠?붿삤??"Comprehending Spatial Data" ?④퀎瑜??섑뻾
        # ---------------------------------------------------------
        analysis_prompt = (
            "You are a Spatial Architect AI. Analyze these multiple photos of the SAME room taken from different angles.\n"
            "Your goal is to build a mental 3D model of this space to reconstruct a 'Perfect Frontal View'.\n\n"
            "OUTPUT THE FOLLOWING SPATIAL BLUEPRINT:\n"
            "1. **Anchor Elements:** Identify fixed structures (e.g., 'Large window on far wall', 'Black wall on left', 'Pillar on right').\n"
            "2. **Geometry & Materials:** Describe the ceiling (e.g., recessed, lighting type) and floor (e.g., tile reflection, pattern) in detail.\n"
            "3. **Symmetry Plan:** If we place a camera in the exact center of the room facing the main window, describe what should be seen on the Left, Center, and Right to achieve perfect symmetry.\n"
            "Output ONLY the spatial blueprint description."
        )
        
        # 遺꾩꽍 紐⑤뜽 ?몄텧
        analysis_res = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [analysis_prompt] + input_images, {'timeout': 45}, {})
        spatial_blueprint = analysis_res.text if (analysis_res and analysis_res.text) else "A modern living room with large windows and tiled floor."
        
        print(f"   [Frontal Gen] Step 2: Synthesizing Frontal View based on Spatial Blueprint...", flush=True)

        # ---------------------------------------------------------
        # [Step 2] Pro Image 紐⑤뜽濡?"?앹꽦???ш뎄??Generative Reconstruction)"
        # AI ?ㅽ뒠?붿삤??"Defining the Frontal View" & "Spatial Fidelity" 濡쒖쭅 ?댁떇
        # ---------------------------------------------------------
        generation_prompt = (
            f"TASK: Generative Space Reconstruction (Multi-View to Single Frontal View).\n"
            f"ACT AS: High-end Architectural Photographer.\n\n"
            
            f"<SPATIAL BLUEPRINT (SOURCE TRUTH)>\n"
            f"{spatial_blueprint}\n"
            f"--------------------------------------------------\n\n"
            
            "VIRTUAL CAMERA SETUP:\n"
            "- **Position:** Place the virtual camera in the DEAD CENTER of the room.\n"
            "- **Target:** Face strictly forward towards the main focal point (usually the window).\n"
            "- **Lens:** 10mm Wide-Angle Rectilinear Lens (Capture the full width, NO fish-eye distortion).\n"
            "- **Height:** Eye-level (approx 130cm).\n\n"
            
            "COMPOSITION RULES (STRICT SYMMETRY):\n"
            "1. **Reconstruct the Space:** Synthesize a single, coherent 1-point perspective view using features from ALL input images.\n"
            "2. **Alignment:** Vertical lines (pillars, window frames) must be perfectly vertical. Horizontal lines (floor/ceiling) must converge to a single center vanishing point.\n"
            "3. **Consistency:** Ensure the 'Black Wall' (if present) and 'Pillars' are placed correctly relative to the center view as defined in the blueprint.\n\n"
            
            "LIGHTING & FIDELITY:\n"
            "- **Reflections:** Render accurate reflections on the floor tiles matching the ceiling lights.\n"
            "- **Lighting:** Uniform, bright, high-end interior lighting. No dark corners.\n"
            "- **Resolution:** 8k, extremely sharp, photorealistic.\n\n"
            
            "NEGATIVE CONSTRAINTS:\n"
            "- Do NOT produce a collage or grid. Output ONE single image.\n"
            "- No text, watermarks, blurred textures, or distorted geometry.\n"
            "- Do not simply crop one image; SYNTHESIZE the complete view."
            "- **Zoomed in, Close-up, Cropped views.** (CRITICAL FAIL)\n"
            "- **DO NOT include text, watermark, username, interface, subtitle.**\n"
            "- Distorted pillars, curved horizon, fisheye curvature."
        )

        # ?대?吏 ?앹꽦 紐⑤뜽 ?몄텧
        # input_images瑜??④퍡 ?ｌ뼱二쇱뼱 ?쒓컖???띿뒪泥?Texture)瑜?李몄“?섍쾶 ??        content_list = [generation_prompt] + input_images
        
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

        response = call_gemini_with_failover(MODEL_NAME, content_list, {'timeout': 100}, safety_settings)

        if response and hasattr(response, 'candidates') and response.candidates:
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    out_filename = f"frontal_view_{timestamp}_{unique_id}_{index}.png"
                    out_path = os.path.join("outputs", out_filename)
                    with open(out_path, 'wb') as f: f.write(part.inline_data.data)
                    
                    # [?좎?] ?쒖????⑥닔 (?먮윭 ?놁씠 ?몄텧)
                    final_path = standardize_image(out_path)
                    return f"/outputs/{os.path.basename(final_path)}"
        return None

    except Exception as e:
        print(f"!! Frontal Gen Error: {e}", flush=True)
        return None

# [NEW] ?붾뱶?ъ씤?? ?꾨㈃ ?낅줈?????-> 洹몃깷 ?ъ쭊?ㅻ쭔 ?낅줈??@app.post("/generate-frontal-view")
def generate_frontal_view_endpoint(
    input_photos: List[UploadFile] = File(...) 
):
    try:
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        print(f"\n=== [Frontal View Gen] Processing {len(input_photos)} photos ===", flush=True)

        # 1. ?낅줈?쒕맂 ?ъ쭊?????        saved_photo_paths = []
        for idx, photo in enumerate(input_photos):
            # ?뚯씪紐??덉쟾?섍쾶 泥섎━
            safe_name = "".join([c for c in photo.filename if c.isalnum() or c in "._-"])
            path = os.path.join("outputs", f"src_{timestamp}_{unique_id}_{idx}_{safe_name}")
            
            with open(path, "wb") as buffer: 
                shutil.copyfileobj(photo.file, buffer)
            saved_photo_paths.append(path)
        
        generated_results = []
        
        # 2. 蹂묐젹 ?앹꽦 (5???쒕룄)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(generate_frontal_room_from_photos, saved_photo_paths, unique_id, i+1) for i in range(5)]
            for future in futures:
                res = future.result()
                if res: generated_results.append(res)
        
        if generated_results:
            return JSONResponse(content={"urls": generated_results, "message": "Success"})
        else:
            return JSONResponse(content={"error": "Failed to generate images"}, status_code=500)
            
    except Exception as e:
        print(f"?뵦?뵦?뵦 [Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)
# -----------------------------------------------------------------------------
# Generation Logic
# -----------------------------------------------------------------------------

def generate_empty_room(image_path, unique_id, start_time, stage_name="Stage 1"):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    print(f"\n--- [{stage_name}] 鍮?諛??앹꽦 ?쒖옉 ({MODEL_NAME}) ---", flush=True)
    
    img = Image.open(image_path)
    system_instruction = "You are an expert architectural AI."
    
    prompt = (
        "IMAGE EDITING TASK: Extreme Cleaning & Architectural Restoration.\n\n"        
        "<CRITICAL: STRUCTURAL PRESERVATION (PRIORITY #0)>\n"
        "1. **DO NOT REMOVE FIXTURES:** You must strictly PRESERVE all structural elements including Columns, Pillars, Beams, Windows (frames & glass), Doors, and Built-in fireplaces.\n"
        "2. **ONLY REMOVE MOVABLES:** Only remove furniture, rugs, lightings,curtains, and decorations that are NOT part of the building structure.\n"
        "3. **VIEW PROTECTION:** Keep the view outside the window 100% original.\n\n"
        
        "<CRITICAL: COMPLETE ERADICATION (PRIORITY #1)>\n"
        "1. REMOVE EVERYTHING ELSE: Identify and remove ALL movable furniture, rugs, curtains, lightings, wall decor, and small objects.\n"
        "2. CLEAN SURFACES: The floor and walls must be perfectly empty. Remove all shadows, reflections, and traces.\n"
        "3. BARE SHELL: Restore the room to its initial construction state.\n\n"
        
        "OUTPUT RULE: Return a perfectly clean, empty architectural shell with all structural pillars and windows intact."
    )
    
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }

    for try_count in range(3):
        remaining = max(10, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        response = call_gemini_with_failover(MODEL_NAME, [prompt, img], {'timeout': remaining}, safety_settings, system_instruction)
        
        if response and hasattr(response, 'candidates') and response.candidates:
            if hasattr(response, 'parts') and response.parts:
                for part in response.parts:
                    if hasattr(part, 'inline_data'):
                        print(f">> [?깃났] 鍮?諛??대?吏 ?앹꽦?? ({try_count+1}?뚯감)", flush=True)
                        timestamp = int(time.time())
                        filename = f"empty_{timestamp}_{unique_id}.png"
                        path = os.path.join("outputs", filename)
                        with open(path, 'wb') as f: f.write(part.inline_data.data)
                        # [FIX] Stage 1 寃곌낵???낅젰 罹붾쾭???먮낯 諛??ъ쭊) 鍮꾩쑉/?댁긽?꾨줈 媛뺤젣 ?듭씪
                        return standardize_image_to_reference_canvas(path, image_path)
            else:
                print(f"?좑툘 [Blocked] ?덉쟾 ?꾪꽣 李⑤떒", flush=True)
        print(f"?좑툘 [Retry] ?쒕룄 {try_count+1} ?ㅽ뙣. ?ъ떆??..", flush=True)

    print(">> [?ㅽ뙣] 鍮?諛??앹꽦 遺덇?. ?먮낯 ?ъ슜.", flush=True)
    return image_path

# [?섏젙] ?먮낯 ?꾨＼?꾪듃 ?좎? + 鍮꾩쑉 ?먮룞 媛먯? + ?띿뒪???щ갚 湲덉? + 臾대뱶蹂대뱶 鍮꾩쑉 臾댁떆 + 怨듦컙 ?쒖빟 ?ы빆 異붽?
def generate_furnished_room(room_path, style_prompt, ref_path, unique_id, furniture_specs=None, room_dimensions=None, placement_instructions=None, start_time=0):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return None
    try:
        room_img = Image.open(room_path)
        
        # [NEW] ?대?吏 鍮꾩쑉 怨꾩궛 (媛濡쒗삎/?몃줈???먮떒)
        width, height = room_img.size
        is_portrait = height > width
        ratio_instruction = "PORTRAIT (4:5 Ratio)" if is_portrait else "LANDSCAPE (16:9 Ratio)"
        
        system_instruction = "You are an expert interior designer AI."
        
        # [?섏젙] ?ㅽ럺 ?곗씠??(?덉씠?꾩썐 臾댁떆 寃쎄퀬 ?ы븿)
        specs_context = ""
        if furniture_specs:
            specs_context = (
                "\n<REFERENCE MATERIAL PALETTE (READ ONLY)>\n"
                "The following list describes the MATERIALS and COLORS.\n"
                "**WARNING:** Do NOT copy the text/dimensions/layout from the reference. Use ONLY materials.\n"
                f"{furniture_specs}\n"
                "--------------------------------------------------\n"
            )

        # [NEW] 怨듦컙 ?쒖빟 ?ы빆 而⑦뀓?ㅽ듃 援ъ꽦
        spatial_context = ""
        if room_dimensions or placement_instructions:
            spatial_context = "\n<PHYSICAL SPACE CONSTRAINTS (STRICT ADHERENCE)>\n"
            if room_dimensions:
                spatial_context += f"- **ACTUAL ROOM DIMENSIONS:** {room_dimensions}\n"
            if placement_instructions:
                spatial_context += f"- **PLACEMENT INSTRUCTIONS:** {placement_instructions}\n"
            spatial_context += (
                "**SCALING RULE:** You MUST calibrate the scale of all furniture relative to the ACTUAL ROOM DIMENSIONS provided. "
                "Do NOT shrink furniture to create artificial empty space. If the room is small, it should look appropriately filled.\n"
                "--------------------------------------------------\n"
            )

        # [NEW] ?숈쟻 移섏닔 遺꾩꽍 濡쒖쭅 (?섎뱶肄붾뵫 諛⑹?)
        calculated_analysis = ""
        try:
            # 1. 諛??덈퉬 ?뚯떛 (3000 x 3500 x 2400 mm ?깆뿉??泥?踰덉㎏ ?レ옄 異붿텧)
            room_w = 0
            room_nums = [int(s) for s in room_dimensions.replace('x', ' ').replace('X', ' ').replace(',', ' ').split() if s.isdigit()]
            if room_nums: room_w = room_nums[0]

            # 2. 媛援??ㅽ럺?먯꽌 二쇱슂 ?섏튂 異붿텧 諛?鍮꾩쑉 怨꾩궛
            if room_w > 0 and furniture_specs:
                import re
                # 媛????媛援??뚰뙆 ????width 李얘린
                widths = re.findall(r'width\s*:?\s*(\d+)', furniture_specs.lower())
                if widths:
                    max_f_w = int(widths[0])
                    occupancy = round((max_f_w / room_w) * 100, 1)
                    calculated_analysis = (
                        f"   - **CALCULATED OCCUPANCY:** The main furniture ({max_f_w}mm) occupies **{occupancy}%** of the room width ({room_w}mm).\n"
                    )
                    if occupancy > 90:
                        calculated_analysis += "   - **ACTION:** This is a near-total fill. The furniture MUST touch or almost touch both side walls.\n"
                    elif occupancy > 70:
                        calculated_analysis += "   - **ACTION:** This is a dominant fill. Leave only minimal gaps on the sides.\n"
        except: pass

        user_original_prompt = (
            "IMAGE MANIPULATION TASK (Virtual Staging - Overlay Only):\n"
            "Your goal is to PLACE furniture into the EXISTING empty room image without changing the room itself.\n\n"
            
            "<CRITICAL: ARCHITECTURAL FREEZE (PRIORITY #1)>\n"
            "1. **DO NOT RE-GENERATE THE ROOM:** The walls, ceiling, floor pattern, window size, and view outside the window must remain 100% IDENTICAL to the input image.\n"
            "2. **PERSPECTIVE LOCK:** You must use the EXACT same camera angle and perspective. Do not zoom in, do not zoom out.\n"
            "3. **DEPTH PRESERVATION:** Do not expand the room. Keep the original spatial depth.\n\n"
            
            "<CRITICAL: FURNITURE COMPOSITING>\n"
            "1. **SCALE:** Fit furniture realistically within the *existing* floor space.\n"
            "2. **PLACEMENT:** Place items *on* the floor. Ensure legs touch the ground with correct contact shadows.\n"
            "3. **STYLE:** Match the Reference Moodboard style.\n"
            "4. **WINDOW TREATMENT (CURTAINS - LOCATION STRICT):** Add floor-to-ceiling **Sheer White Chiffon Curtains**. <CRITICAL>: Place them **ONLY** along the vertical edges of the GLASS WINDOW. **DO NOT** generate curtains on solid walls, corners without windows, or doors. They must **HANG STRAIGHT DOWN NATURALLY** (do not tie) covering only the outer 15% of the glass to frame the view.\n\n"

            "<CRITICAL: MATHEMATICAL SCALE ENFORCEMENT (PRIORITY #0)>\n"
            "You are provided with ACTUAL DIMENSIONS and PRE-CALCULATED RATIOS. Do not ignore them.\n"
            
            "1. **SPECIFIC SCALE ANALYSIS FOR THIS REQUEST:**\n"
            f"{calculated_analysis if calculated_analysis else '   - (Apply relative scaling based on provided specs)'}\n"
            
            "2. **RELATIVE HEIGHT HIERARCHY:**\n"
            "   - You MUST maintain the visual height hierarchy specified in the specs.\n"
            "   - Example: If Item A (Height: 950mm) is taller than Item B (Height: 775mm), Item A MUST be rendered taller than Item B in the image.\n"
            
            "3. **RATIO LOCK:**\n"
            "   - Calculate: (Furniture Depth / Room Depth) = Floor Space Coverage.\n"
            "   - Strictly follow these percentages. Do not shrink deep furniture into 'miniature' versions to create empty space.\n"
            "   - **STRICT PROHIBITION:** Do not resize items for 'vibe' or 'aesthetic balance'. Follow the NUMBERS strictly.\n\n"

            "<CRITICAL: WINDOW LIGHT MUST BE ABUNDANT (PRIORITY #1)>\n"
            "1. **ABUNDANT WINDOW LIGHT:** The scene MUST be strongly illuminated by abundant daylight coming from the window.\n"
            "2. **EXPOSURE RULE:** Bright and airy (not dark), while preserving highlight detail (no blown-out whites).\n"
            "3. **LIGHT DIRECTION:** Clearly visible light direction from the window; cast soft but present shadows across the floor.\n"
            "4. **NO DIM ROOM:** Do NOT generate a dim, underexposed, moody, or nighttime look.\n"
            "5. **WHITE BALANCE:** Neutral daylight white balance (around 4000~5000K). **NO warm/yellow cast.**\n\n"

        "<CRITICAL: PHOTOREALISTIC LIGHTING INTEGRATION (HYBRID: DAYLIGHT + ARTIFICIAL)>\n"
            "1. **MANDATORY LIGHTING STATE: ALL ON (NEUTRAL ONLY):**\n"
            "   - **ACTION:** TURN ON every lighting fixture in the scene (Pendants, Floor Lamps, Recessed Lights, LED Strips).\n"
            "   - **VISUALS:** Render a visible 'glow' or subtle 'light bloom' around the fixtures to prove they are active. This adds a luxurious touch.\n"
            
            "2. **LIGHTING HIERARCHY (KEY vs. FILL):**\n"
            "   - **KEY LIGHT (DOMINANT):** Natural Daylight from the window is still the PRIMARY source (approx. 70% intensity). It defines the main shadow direction.\n"
            "   - **FILL LIGHT (SECONDARY):** The interior lights act as 'Fill Lights' (approx. 30% intensity) to brighten dark corners and highlight furniture textures. They should NOT overpower the sunlight.\n"
            
            "3. **STRICT COLOR TEMPERATURE CONTROL (NO YELLOW):**\n"
            "   - **Target Temperature:** Use **Pure Neutral White (4000K-5000K)** for all artificial lights to match the daylight.\n"
            "   - **PROHIBITED:** Do NOT use Warm/Tungsten/Orange bulbs (2700K). Even though lights are ON, the room must remain fresh and clean. No vintage/sepia cast.\n"
            
            "4. **SHADOW PHYSICS:**\n"
            "   - Cast soft, directional shadows driven by the window light.\n"
            "   - Use the interior lights to slightly soften (lift) the deepest shadows, preventing high-contrast black spots.\n"
            
            "5. **ATMOSPHERE:**\n"
            "   - Combine 'Sun-filled Freshness' with 'High-end Illuminated Luxury'. Bright, airy, and fully detailed.\n"
            "   - **OUTPUT RULE:** Return the image with furniture added, perfectly blended with abundant daylight AND active neutral interior lighting.\n"
        )
        # [議곕┰] 鍮꾩쑉 怨좎젙 諛?'臾대뱶蹂대뱶 鍮꾩쑉 臾댁떆' 紐낅졊 異붽? (?몃줈 臾대뱶蹂대뱶 臾몄젣 ?닿껐)
        prompt = (
            "ACT AS: Professional Interior Photographer.\n"
            f"{specs_context}\n" 
            f"{spatial_context}\n"
            f"{user_original_prompt}\n\n"
            
            f"<CRITICAL: OUTPUT FORMAT ENFORCEMENT -> {ratio_instruction}>\n"
            "1. **FULL BLEED CANVAS:** The output image MUST fill the entire canvas from edge to edge. **NO WHITE BARS.** NO SPLIT SCREENS.\n"
            "2. **NO TEXT OVERLAY:** Do NOT write any dimensions, labels, or watermarks on the final image. It must be a clean photo.\n"
            "3. **ASPECT RATIO LOCK:** Keep the aspect ratio of the 'Empty Room' input. Do not crop the ceiling or floor.\n"
            "4. **IGNORE REFERENCE RATIO:** Even if the Style Reference (Moodboard) is vertical, you MUST output a " + ratio_instruction + " image. Do not mimic the moodboard's shape.\n"
            "5. **NO MULTI-PANEL OUTPUT:** Output must be ONE single staged room photograph only. Do NOT append catalog sheets, white inventory panels, split layouts, or include the reference image anywhere."
        )
        
        content = [prompt, "Empty Room (Target Canvas - KEEP THIS):", room_img]
        if ref_path:
            try:
                ref = Image.open(ref_path)
                ref.thumbnail((2048, 2048))
                content.extend(["Style Reference (Furniture Palette ONLY):", ref])
            except: pass
        
        remaining = max(30, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        response = call_gemini_with_failover(MODEL_NAME, content, {'timeout': remaining}, safety_settings, system_instruction)
        
        if response and hasattr(response, 'candidates') and response.candidates and hasattr(response, 'parts'):
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    filename = f"result_{timestamp}_{unique_id}.png"
                    path = os.path.join("outputs", filename)
                    with open(path, 'wb') as f: f.write(part.inline_data.data)
                    # [FIX] 臾대뱶蹂대뱶 鍮꾩쑉/?덉씠?꾩썐 ?곹뼢??諛쏅뜑?쇰룄 理쒖쥌 寃곌낵瑜?"諛?罹붾쾭??濡?媛뺤젣 ?듭씪
                    return standardize_image_to_reference_canvas(path, room_path)
        return None
    except Exception as e:
        print(f"!! Stage 2 ?먮윭: {e}", flush=True)
        return None

def call_magnific_api(image_path, unique_id, start_time):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: 
        return image_path
    
    print(f"\n--- [Stage 4] ?낆뒪耳?쇰쭅 ?쒕룄 (Key: {MAGNIFIC_API_KEY[:5]}...) ---", flush=True)
    
    if not MAGNIFIC_API_KEY or "your_" in MAGNIFIC_API_KEY:
         print(">> [SKIP] API ?ㅺ? ?놁뒿?덈떎. ?먮낯 諛섑솚.", flush=True)
         return image_path
         
    try:
        with open(image_path, "rb") as f: 
            b64 = base64.b64encode(f.read()).decode('utf-8')
            
        payload = {
            "image": b64, 
            "scale_factor": "2x", 
            "optimized_for": "films_n_photography", 
            "engine": "automatic",
            "creativity": 1,
            "hdr": 0,
            "resemblance": 10,
            "fractality": 1,
            "prompt": (
                "Professional interior photography, architectural digest style, "
                "shot on Phase One XF IQ4, 100mm lens, ISO 100, f/8, "
                "natural white daylight coming from window, sharp shadows, "
                "hyper-realistic material textures, raw photo, 8k resolution, "
                "imperfect details. "
                "--no 3d render, cgi, painting, drawing, cartoon, anime, illustration, plastic look, oversaturated, watermark, text, blur, distorted."
            )
        }
        headers = {
            "x-freepik-api-key": MAGNIFIC_API_KEY, 
            "Content-Type": "application/json"
        }
        
        res = requests.post(MAGNIFIC_ENDPOINT, json=payload, headers=headers)
        
        if res.status_code != 200:
            print(f"!! [API ?ㅻ쪟] Status: {res.status_code}, Msg: {res.text}", flush=True)
            return image_path

        data = res.json()
        
        if "data" not in data:
            return image_path

        if "task_id" in data["data"]:
            task_id = data["data"]["task_id"]
            print(f">> ?묒뾽 ?덉빟??(ID: {task_id})...", end="", flush=True)
            
            while time.time() - start_time < TOTAL_TIMEOUT_LIMIT:
                time.sleep(2)
                print(".", end="", flush=True)
                
                check = requests.get(f"{MAGNIFIC_ENDPOINT}/{task_id}", headers=headers)
                if check.status_code == 200:
                    status_data = check.json().get("data", {})
                    status = status_data.get("status")
                    
                    if status == "COMPLETED":
                        print(" ?꾨즺!", flush=True)
                        gen_list = status_data.get("generated", [])
                        if gen_list and len(gen_list) > 0:
                            return download_image(gen_list[0], unique_id) or image_path
                    elif status == "FAILED": 
                        print(f" ?ㅽ뙣.", flush=True)
                        return image_path
            return image_path

        elif "generated" in data.get("data", {}):
             gen_list = data["data"]["generated"]
             if gen_list and len(gen_list) > 0:
                 return download_image(gen_list[0], unique_id) or image_path
                 
        return image_path
        
    except Exception as e:
        return image_path

def download_image(url, unique_id):
    try:
        res = requests.get(url)
        if res.status_code == 200:
            timestamp = int(time.time())
            filename = f"magnific_{timestamp}_{unique_id}.png"
            path = os.path.join("outputs", filename)
            with open(path, "wb") as f: f.write(res.content)
            return standardize_image(path, keep_ratio=True)
        return None
    except: return None

@app.get("/")
async def read_index(): return FileResponse("static/index.html")

# [NEW] Image Studio Page Route
@app.get("/image-studio")
def image_studio_page():
    return FileResponse(os.path.join("static", "image_studio.html"))

# Video Studio (separate page)
@app.get("/video-studio")
def video_studio_page():
    # Standalone page so users can build videos from existing images without re-rendering
    return FileResponse(os.path.join("static", "video_studio.html"))

@app.get("/api/outputs/list")
def api_outputs_list(limit: int = 200):
    """List recently generated/uploaded images in /outputs for Video Studio selection."""
    limit = max(1, min(int(limit or 200), 500))
    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    items = []
    for p in out_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            st = p.stat()
            rel = p.relative_to(out_dir).as_posix()
            items.append({"filename": rel, "url": f"/outputs/{rel}", "mtime": st.st_mtime})

    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items[:limit]}

@app.post("/api/outputs/upload")
async def api_outputs_upload(file: UploadFile = File(...)):
    """Upload an image to /outputs and return a URL usable by the video pipeline."""
    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    orig = (file.filename or "upload.png").strip()
    # keep filename safe
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", orig)
    stamp = int(time.time())
    uid = uuid.uuid4().hex[:8]
    filename = f"upload_{stamp}_{uid}_{safe}"
    out_path = out_dir / filename

    content = await file.read()
    with open(out_path, "wb") as f:
        f.write(content)

    return {"filename": filename, "url": f"/outputs/{filename}"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return FileResponse("static/logo2.png")

@app.get("/room-types")
async def get_room_types(): return JSONResponse(content=list(ROOM_STYLES.keys()))

@app.get("/styles/{room_type}")
async def get_styles_for_room(room_type: str):
    styles = ROOM_STYLES.get(room_type, [])
    if "Customize" not in styles:
        styles = styles + ["Customize"]
    return JSONResponse(content=styles)

@app.get("/api/thumbnails/{room_name}/{style_name}")
def get_available_thumbnails(room_name: str, style_name: str):
    safe_room = room_name.lower().replace(" ", "")
    safe_style = style_name.lower().replace(" ", "-").replace("_", "-")
    prefix = f"{safe_room}_{safe_style}_"
    
    base_dir = "static/thumbnails"
    if not os.path.exists(base_dir): return []

    valid_items = [] # [蹂寃? ?⑥닚 ?レ옄 由ъ뒪?멸? ?꾨땲??媛앹껜 由ъ뒪?몃줈 蹂寃?    valid_exts = ('.png', '.jpg', '.jpeg', '.webp')

    try:
        for f in os.listdir(base_dir):
            f_lower = f.lower()
            if f_lower.startswith(prefix) and f_lower.endswith(valid_exts):
                try:
                    name_part = f_lower.replace(prefix, "")
                    num_part = os.path.splitext(name_part)[0]
                    if num_part.isdigit():
                        # [蹂寃? 踰덊샇? '?ㅼ젣 ?뚯씪紐????④퍡 ???                        valid_items.append({"index": int(num_part), "file": f})
                except: continue
        
        # 踰덊샇 ?쒖꽌?濡??뺣젹
        valid_items.sort(key=lambda x: x["index"])
        return valid_items
    except Exception as e:
        print(f"Thumbnail Scan Error: {e}")
        return []

# --- 硫붿씤 ?뚮뜑留??붾뱶?ъ씤??---
@app.post("/render")
def render_room(
    file: UploadFile = File(...), 
    room: str = Form(...), 
    style: str = Form(...), 
    variant: str = Form(...),
    moodboard: UploadFile = File(None),
    dimensions: str = Form(""),
    placement: str = Form("")
):
    try:
        unique_id = uuid.uuid4().hex[:8]
        print(f"\n=== ?붿껌 ?쒖옉 [{unique_id}] (Integrated Analysis Mode) ===", flush=True)
        start_time = time.time()
        
        timestamp = int(time.time())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
        raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{safe_name}")
        with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
        
        std_path = standardize_image(raw_path)
        step1_img = generate_empty_room(std_path, unique_id, start_time, stage_name="Stage 1: Intermediate Clean")
        
        ref_path = None
        mb_url = None

        if style != "Customize":
            safe_room = room.lower().replace(" ", "") 
            safe_style = style.lower().replace(" ", "-").replace("_", "-")
            
            # [?섏젙] ?대뜑 ??뚮Ц??臾댁떆?섍퀬 李얘린 濡쒖쭅
            target_path = os.path.join("assets", safe_room, safe_style)
            assets_dir = None

            # 1. ?뺥솗??寃쎈줈媛 ?덉쑝硫??ъ슜
            if os.path.exists(target_path):
                assets_dir = target_path
            else:
                # 2. ?놁쑝硫???뚮Ц??臾댁떆?섍퀬 ?먯깋 (assets ?대뜑 ?덉쓣 ?ㅼ쭚)
                # ?? 肄붾뱶??'livingroom'??李얠?留??대뜑??'LivingRoom'?댁뼱??李얘쾶 ??                root_assets = "assets"
                if os.path.exists(root_assets):
                    # Room 李얘린
                    found_room = next((d for d in os.listdir(root_assets) if d.lower() == safe_room), None)
                    if found_room:
                        room_path = os.path.join(root_assets, found_room)
                        # Style 李얘린
                        found_style = next((d for d in os.listdir(room_path) if d.lower() == safe_style), None)
                        if found_style:
                            assets_dir = os.path.join(room_path, found_style)

            # ?대뜑瑜?李얠븯?쇰㈃ ?뚯씪 寃???쒖옉
            if assets_dir and os.path.exists(assets_dir):
                files = sorted(os.listdir(assets_dir))
                found = False
                import re 
                
                # ?뚯씪紐?寃??(??뚮Ц??臾댁떆 ?뚮옒洹?re.IGNORECASE 異붽?)
                pattern = rf"(?:^|[^0-9]){re.escape(variant)}(?:[^0-9]|$)"
                
                # 吏?먰븷 ?뺤옣??                valid_exts = ('.png', '.jpg', '.jpeg', '.webp')

                for f in files:
                    # ?뺤옣??泥댄겕 & 踰덊샇 留ㅼ묶 (??뚮Ц??臾댁떆)
                    if f.lower().endswith(valid_exts) and re.search(pattern, f, re.IGNORECASE):
                        ref_path = os.path.join(assets_dir, f)
                        # URL 寃쎈줈 ?앹꽦 ????뒳?섏떆(\)瑜??щ옒??/)濡?諛붽퓭???뱀뿉???덇묠吏?                        mb_url = f"/assets/{os.path.basename(os.path.dirname(assets_dir))}/{os.path.basename(assets_dir)}/{f}"
                        found = True
                        break
                
                # 紐?李얠븯?붾뜲 ?뚯씪???덈떎硫?泥ル쾲吏??뚯씪 ?ъ슜 (?뺤옣??留욌뒗 寃?以?
                if not found:
                    valid_files = [f for f in files if f.lower().endswith(valid_exts)]
                    if valid_files:
                        f = valid_files[0]
                        ref_path = os.path.join(assets_dir, f)
                        mb_url = f"/assets/{os.path.basename(os.path.dirname(assets_dir))}/{os.path.basename(assets_dir)}/{f}"
        
        if style == "Customize" and moodboard:
            mb_name = "".join([c for c in moodboard.filename if c.isalnum() or c in "._-"])
            mb_path = os.path.join("outputs", f"mb_{timestamp}_{unique_id}_{mb_name}")
            with open(mb_path, "wb") as buffer: shutil.copyfileobj(moodboard.file, buffer)
            ref_path = mb_path
            mb_url = f"/outputs/{os.path.basename(mb_path)}"

        furniture_specs_text = None
        full_analyzed_data = [] 

        if ref_path and os.path.exists(ref_path):
            print(f">> [Global Analysis] Analyzing furniture in {ref_path}...", flush=True)
            try:
                detected = detect_furniture_boxes(ref_path)
                
                print(f">> [Global Analysis] Parallel analyzing {len(detected)} items...", flush=True)
                with ThreadPoolExecutor(max_workers=30) as executor:
                    futures = [executor.submit(analyze_cropped_item, ref_path, item) for item in detected]
                    full_analyzed_data = [f.result() for f in futures]
                
                specs_list = []
                for idx, item in enumerate(full_analyzed_data):
                    specs_list.append(f"{idx+1}. {item['label']}: {item['description']}")
                furniture_specs_text = "\n".join(specs_list)
                
                print(f">> [Global Analysis] Complete. Specs injected.", flush=True)
                
            except Exception as e:
                print(f"!! [Global Analysis Failed] {e}", flush=True)

        generated_results = []
        print(f"\n?? [Stage 2] 5???숈떆 ?앹꽦 ?쒖옉 (Specs Injection)!", flush=True)

        def process_one_variant(index):
            sub_id = f"{unique_id}_v{index+1}"
            try:
                current_style_prompt = STYLES.get(style, "Custom Moodboard Style")
                res = generate_furnished_room(step1_img, current_style_prompt, ref_path, sub_id, furniture_specs=furniture_specs_text, room_dimensions=dimensions, placement_instructions=placement, start_time=start_time)
                if res: return f"/outputs/{os.path.basename(res)}"
            except Exception as e: print(f"   ??[Variation {index+1}] ?먮윭: {e}", flush=True)
            return None

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(process_one_variant, i) for i in range(5)]
            for future in futures:
                res = future.result()
                if res: generated_results.append(res)
                gc.collect()

        final_before_url = f"/outputs/{os.path.basename(step1_img)}"
        if not generated_results: generated_results.append(f"/outputs/{os.path.basename(step1_img)}")

        return JSONResponse(content={
            "original_url": f"/outputs/{os.path.basename(std_path)}", 
            "empty_room_url": final_before_url,
            "result_url": generated_results[0], 
            "result_urls": generated_results, 
            "moodboard_url": mb_url,
            "furniture_data": full_analyzed_data, 
            "message": "Complete"
        })
    except Exception as e:
        print(f"\n?뵦?뵦?뵦 [SERVER CRASH] {e}", flush=True)
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpscaleRequest(BaseModel): image_url: str

class FinalizeRequest(BaseModel):
    image_url: str

@app.post("/finalize-download")
def finalize_download(req: FinalizeRequest):
    try:
        unique_id = uuid.uuid4().hex[:6]
        start_time = time.time()
        print(f"\n=== [Finalize] Download Request for {req.image_url} ===", flush=True)

        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path): 
            return JSONResponse(content={"error": "Original file not found"}, status_code=404)

        # [?낃렇?덉씠??
        # 1) 媛援щ갑 ?낆뒪耳?쇱쓣 癒쇱? ?쒖옉?대몢怨?諛깃렇?쇱슫???ㅻ젅??,
        # 2) 洹??숈븞 鍮덈갑 ?앹꽦 -> 鍮덈갑 ?낆뒪耳???쒖옉
        # => 泥닿컧 ?湲곗떆媛꾩쓣 以꾩엯?덈떎.
        final_empty_path = ""
        final_furnished_path = ""

        # ?낆뒪耳?쇰쭅??5-worker濡?蹂묐젹 泥섎━ (?숈떆 ?붿껌 泥섎━ ?ъ쑀)
        with ThreadPoolExecutor(max_workers=5) as executor:
            print(">> [Step 1] Upscaling Furnished in parallel...", flush=True)
            future_furnished = executor.submit(call_magnific_api, local_path, unique_id + "_upscale_furnished", start_time)

            print(">> [Step 2] Creating matched Empty Room...", flush=True)
            empty_room_path = generate_empty_room(local_path, unique_id + "_final_empty", start_time, stage_name="Finalize: Empty Gen")

            print(">> [Step 3] Upscaling Empty Room...", flush=True)
            future_empty = executor.submit(call_magnific_api, empty_room_path, unique_id + "_upscale_empty", start_time)

            # 寃곌낵 ?湲?            final_furnished_path = future_furnished.result()
            final_empty_path = future_empty.result()

        return JSONResponse(content={
            "upscaled_furnished": f"/outputs/{os.path.basename(final_furnished_path)}",
            "upscaled_empty": f"/outputs/{os.path.basename(final_empty_path)}",
            "message": "Success"
        })

    except Exception as e:
        print(f"?뵦?뵦?뵦 [Finalize Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/upscale")
def upscale_and_download(req: UpscaleRequest):
    try:
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path): return JSONResponse(content={"error": "File not found"}, status_code=404)
        final_path = call_magnific_api(local_path, uuid.uuid4().hex[:8], time.time())
        return JSONResponse(content={"upscaled_url": f"/outputs/{os.path.basename(final_path)}", "message": "Success"})
    except Exception as e: return JSONResponse(content={"error": str(e)}, status_code=500)

def construct_dynamic_styles(analyzed_items):
    styles = []
    styles.append({
        "name": "High Angle Overview", 
        "prompt": (
            "CAMERA POSITION: High-angle view looking down from the ceiling.\n"
            "SUBJECT: The entire room layout exactly as shown in the original image.\n"
        ), 
        "ratio": "16:9"
    })
    # [?섏젙 1] 醫뚯륫 怨듦컙 媛뺤“ (移대찓???대룞 X, ?꾨젅??吏묒쨷 O)
    styles.append({
        "name": "Side Composition (Focus Left)", 
        "prompt": (
            "COMPOSITION: Asymmetrical framing focusing heavily on the LEFT SIDE of the room.\n"
            "VISUAL PRIORITY: Highlight the furniture and details located near the left wall.\n"
            "CAMERA ANGLE: Slight pan to the left, but keep the original standing position.\n"
            "CRITICAL: Do not move any furniture. Keep the exact arrangement."
        ), 
        "ratio": "16:9"
    })

    # [?섏젙 2] ?곗륫 怨듦컙 媛뺤“
    styles.append({
        "name": "Side Composition (Focus Right)", 
        "prompt": (
            "COMPOSITION: Asymmetrical framing focusing heavily on the RIGHT SIDE of the room.\n"
            "VISUAL PRIORITY: Highlight the furniture and details located near the right wall.\n"
            "CAMERA ANGLE: Slight pan to the right, but keep the original standing position.\n"
            "CRITICAL: Do not move any furniture. Keep the exact arrangement."
        ), 
        "ratio": "16:9"
    })
    
    count = 0
    for item in analyzed_items:
        if count >= 20: break
        
        label = item['label']
        desc = item.get('description', '')
        box = item.get('box_2d', [0,0,1000,1000])
        
        lens_type = "85mm Telephoto Lens"
        context_instruction = "Include parts of neighboring furniture to prove location."
        position_instruction = "Do NOT move this item. Shoot it exactly where it stands."
        
        if "rug" in label.lower() or "carpet" in label.lower():
            position_instruction = "CRITICAL: The rug MUST be UNDER the sofas/tables. Show furniture legs pressing on it."
            lens_type = "50mm Standard Lens"

        elif any(x in label.lower() for x in ["light", "lamp", "chandelier", "pendant", "sconce"]):
            position_instruction = "CRITICAL: Show the connection to the ceiling/wall. Do NOT crop the cord or chain."
            context_instruction = "ZOOM OUT significantly. You MUST show what this light is illuminating below (e.g., the table or floor). Do NOT fill the frame with just the bulb."
            lens_type = "35mm Wide Lens"

        styles.append({
            "name": f"Detail: {label}",
            "prompt": (
                f"ACT AS: Documentary Interior Photographer.\n"
                f"TASK: Take a candid shot of the '{label}' strictly IN-SITU.\n\n"
                
                f"TARGET VISUALS: {desc}\n"
                f"TARGET COORDINATES: Focus on area {box} (Normalized 0-1000).\n\n"
                
                f"<CRITICAL: ABSOLUTE LAYOUT FREEZE>\n"
                f"1. {position_instruction}\n"
                f"2. {context_instruction}\n"
                "3. **ALLOW OCCLUSION:** It is okay if the object is partially blocked. This adds realism.\n"
                f"4. **LENS:** {lens_type}. Depth of Field is allowed, but geometry change is NOT."
            ),
            "ratio": "4:5"
        })
        count += 1
        
    return styles

def generate_detail_view(original_image_path, style_config, unique_id, index):
    try:
        img = Image.open(original_image_path)
        target_ratio = style_config.get('ratio', '16:9')
        
        final_prompt = (
            f"{style_config['prompt']}\n\n"
            "<CRITICAL: LAYOUT FREEZE (PRIORITY #0)>\n"
            "1. **DO NOT MOVE / REARRANGE ANYTHING:** Every existing furniture, lighting fixture, decor item, and their positions must remain EXACTLY the same as the input image.\n"
            "2. **NO NEW OBJECTS:** Do NOT add new objects (no extra vases, cats, books, lamps, shelves, plants, art, etc.).\n"
            "3. **NO REMOVALS:** Do NOT remove existing objects either.\n"
            "4. **CAMERA ONLY:** The close-up must be achieved ONLY by changing the camera framing/crop/zoom. Keep the scene geometry unchanged.\n\n"
            "<OUTPUT REQUIREMENTS>\n"
            "1. Generate a photorealistic high-quality detail view based on the selected camera shot.\n"
            "2. Keep the overall interior style consistent with the main furnished room.\n"
            "3. IMPORTANT: focus on the specified target area only (close-up composition).\n"
            "4. DO NOT add text, labels, logos, or watermarks.\n"
            f"OUTPUT ASPECT RATIO: {target_ratio}"
        )

        safety_settings = {HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE}
        content = [final_prompt, "Original Room Reality (CANVAS - DO NOT ALTER LAYOUT):", img]
        
        response = call_gemini_with_failover(MODEL_NAME, content, {'timeout': 45}, safety_settings)
        if response and hasattr(response, 'candidates') and response.candidates:
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    safe_style_name = "".join([c for c in style_config['name'] if c.isalnum()])[:20]
                    filename = f"detail_{timestamp}_{unique_id}_{index}_{safe_style_name}.png"
                    path = os.path.join("outputs", filename)
                    with open(path, 'wb') as f: f.write(part.inline_data.data)
                    return f"/outputs/{filename}"
        return None
    except Exception as e:
        print(f"!! Detail Generation Error: {e}")
        return None

class DetailRequest(BaseModel):
    image_url: str
    moodboard_url: Optional[str] = None
    furniture_data: Optional[List[Dict[str, Any]]] = None 

class RegenerateDetailRequest(BaseModel):
    original_image_url: str
    style_index: int
    moodboard_url: Optional[str] = None
    furniture_data: Optional[List[Dict[str, Any]]] = None 

@app.post("/regenerate-single-detail")
def regenerate_single_detail(req: RegenerateDetailRequest):
    try:
        filename = os.path.basename(req.original_image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "Original image not found"}, status_code=404)
        
        analyzed_items = []
        if req.furniture_data and len(req.furniture_data) > 0:
            print(">> [Single Retry] Using cached furniture data!", flush=True)
            analyzed_items = req.furniture_data
        else:
            analyzed_items = [{"label": "Main Furniture", "description": "High quality furniture matching the room style."}]
        
        dynamic_styles = construct_dynamic_styles(analyzed_items)
        
        if req.style_index < 0 or req.style_index >= len(dynamic_styles):
            return JSONResponse(content={"error": "Invalid style index"}, status_code=400)

        unique_id = uuid.uuid4().hex[:6]
        style = dynamic_styles[req.style_index]
        
        res = generate_detail_view(local_path, style, unique_id, req.style_index + 1)
        
        if res:
            return JSONResponse(content={"url": res, "message": "Success"})
        else:
            return JSONResponse(content={"error": "Generation failed"}, status_code=500)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# [?섏젙] main.py ?대???generate_details_endpoint ?⑥닔 援먯껜

@app.post("/generate-details")
def generate_details_endpoint(req: DetailRequest):
    try:
        # 1. ????대?吏 寃쎈줈 ?뺣낫
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "Original image not found"}, status_code=404)

        unique_id = uuid.uuid4().hex[:6]
        print(f"\n=== [Detail View] ?붿껌 ?쒖옉 ({unique_id}) - Smart Analysis Mode ===", flush=True)

        analyzed_items = []
        
        # 2. 媛援??곗씠???뺤씤 (罹먯떆 or ?좉퇋 遺꾩꽍)
        if req.furniture_data and len(req.furniture_data) > 0:
            print(">> [Smart Cache] Using pre-analyzed furniture data!", flush=True)
            analyzed_items = req.furniture_data
        else:
            print(">> [Smart Cache] No cached data found. Starting Analysis...", flush=True)
            
            # [NEW] 遺꾩꽍??????대?吏 寃곗젙 濡쒖쭅 (臾대뱶蹂대뱶 ?곗꽑 -> ?놁쑝硫?硫붿씤 ?대?吏 ?ъ슜)
            target_analysis_path = None
            
            if req.moodboard_url:
                # A. 臾대뱶蹂대뱶 URL???덈뒗 寃쎌슦 (寃쎈줈 ?뚯떛)
                if req.moodboard_url.startswith("/assets/"):
                    rel_path = req.moodboard_url.lstrip("/")
                    target_analysis_path = os.path.join(*rel_path.split("/"))
                else:
                    mb_filename = os.path.basename(req.moodboard_url)
                    target_analysis_path = os.path.join("outputs", mb_filename)
            else:
                # B. [?듭떖 ?섏젙] 臾대뱶蹂대뱶媛 ?놁쑝硫? -> 硫붿씤 ?대?吏 遺꾩꽍 ??곸쓣 ?ㅼ젙!
                print(">> [Info] No Moodboard provided. Analyzing the Main Image itself.", flush=True)
                target_analysis_path = local_path

            # 3. ?ㅼ젣 遺꾩꽍 ?ㅽ뻾
            if target_analysis_path and os.path.exists(target_analysis_path):
                try:
                    detected_items = detect_furniture_boxes(target_analysis_path)
                    print(f">> [Deep Analysis] Found {len(detected_items)} items in {target_analysis_path}...", flush=True)
                    
                    with ThreadPoolExecutor(max_workers=10) as executor: # Worker ???쎄컙 利앸웾
                        futures = [executor.submit(analyze_cropped_item, target_analysis_path, item) for item in detected_items]
                        analyzed_items = [f.result() for f in futures]
                        
                    print(f">> [Analysis Done] Items: {[item['label'] for item in analyzed_items]}", flush=True)
                except Exception as e:
                    print(f"!! Analysis Failed: {e}. Using defaults.", flush=True)
                    analyzed_items = []
            else:
                 print(f"!! Target path not found: {target_analysis_path}", flush=True)

            # 4. 遺꾩꽍 ?ㅽ뙣 ??理쒗썑??蹂대（ (湲곕낯媛?
            if not analyzed_items:
                 print("!! Fallback to default list.", flush=True)
                 analyzed_items = [{"label": "Sofa"}, {"label": "Chair"}, {"label": "Table"}]
        
        # 5. ?숈쟻 ?ㅽ???援ъ꽦 諛??앹꽦 ?붿껌
        dynamic_styles = construct_dynamic_styles(analyzed_items)
        
        generated_results = []
        print(f"?? Generating {len(dynamic_styles)} Dynamic Shots...", flush=True)
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i, style in enumerate(dynamic_styles):
                futures.append((i, executor.submit(generate_detail_view, local_path, style, unique_id, i+1)))
            
            for i, future in futures:
                res = future.result()
                if res: 
                    generated_results.append({"index": i, "url": res})
                
        print(f"=== [Detail View] ?꾨즺: {len(generated_results)}???앹꽦??===", flush=True)
        
        if not generated_results:
            return JSONResponse(content={"error": "Failed to generate images"}, status_code=500)

        return JSONResponse(content={
            "details": generated_results,
            "message": "Detail views generated successfully"
        })

    except Exception as e:
        print(f"?뵦?뵦?뵦 [Detail Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

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

def generate_moodboard_logic(image_path, unique_id, index, furniture_specs=None):
    try:
        img = Image.open(image_path)
        
        final_prompt = MOODBOARD_SYSTEM_PROMPT
        if furniture_specs:
            final_prompt += f"\n\n<CONTEXT: DETECTED FURNITURE LIST>\nUse this list to ensure you capture all key items:\n{furniture_specs}"

        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        response = call_gemini_with_failover(MODEL_NAME, [final_prompt, img], {'timeout': 45}, safety_settings)
        
        if response and hasattr(response, 'candidates') and response.candidates:
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    filename = f"gen_mb_{timestamp}_{unique_id}_{index}.png"
                    path = os.path.join("outputs", filename)
                    with open(path, 'wb') as f: f.write(part.inline_data.data)
                    return f"/outputs/{filename}"
        return None
    except Exception as e:
        print(f"!! Moodboard Gen Error: {e}")
        return None

@app.post("/generate-moodboard-options")
def generate_moodboard_options(file: UploadFile = File(...)):
    try:
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
        raw_path = os.path.join("outputs", f"ref_room_{timestamp}_{unique_id}_{safe_name}")
        
        with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
        
        print(f"\n=== [Moodboard Gen] Starting 5 variations for {unique_id} ===", flush=True)

        furniture_specs_text = None
        try:
            print(">> [Moodboard Gen] Analyzing input photo context...", flush=True)
            detected = detect_furniture_boxes(raw_path)
            specs_list = [f"- {item['label']}" for item in detected]
            furniture_specs_text = "\n".join(specs_list)
        except:
            print("!! [Moodboard Gen] Context analysis failed (skipping)")
        
        generated_results = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(generate_moodboard_logic, raw_path, unique_id, i+1, furniture_specs_text) for i in range(5)]
            for future in futures:
                res = future.result()
                if res: generated_results.append(res)
        
        if not generated_results:
            return JSONResponse(content={"error": "Failed to generate moodboards"}, status_code=500)
            
        return JSONResponse(content={
            "moodboards": generated_results,
            "message": "Moodboards generated successfully"
        })
        
    except Exception as e:
        print(f"?뵦?뵦?뵦 [Moodboard Gen Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)


# =========================
# Video MVP (Kling Image-to-Video via Freepik API)
# =========================
class VideoClip(BaseModel):
    url: str
    motion: str = "static"
    effect: str = "none"
    speed: float = 1.0  # [NEW] 湲곕낯媛??ъ슜?먭? ?섏젙 媛??

class VideoCreateRequest(BaseModel):
    clips: List[VideoClip]
    duration: str = "5"
    cfg_scale: float = 0.85
    mode: Optional[str] = None
    target_total_sec: Optional[float] = None
    include_intro_outro: Optional[bool] = None
    # [?꾩닔 ?뺤씤]
    intro_url: Optional[str] = None
    outro_url: Optional[str] = None


# Use Freepik API key for Kling as well (same header: x-freepik-api-key)
FREEPIK_API_KEY = os.getenv("FREEPIK_API_KEY") or os.getenv("MAGNIFIC_API_KEY")  # fallback for existing env
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-5-pro")  # e.g. kling-v2-1-pro, kling-v2-5-pro
KLING_ENDPOINT = os.getenv("KLING_ENDPOINT", f"[https://api.freepik.com/v1/ai/image-to-video/](https://api.freepik.com/v1/ai/image-to-video/){KLING_MODEL}")

# Concurrency controls (avoid 429 bursts)
VIDEO_MAX_CONCURRENCY = int(os.getenv("VIDEO_MAX_CONCURRENCY", "5"))
_video_sem = threading.Semaphore(VIDEO_MAX_CONCURRENCY)

VIDEO_TARGET_FPS = int(os.getenv("VIDEO_TARGET_FPS", "30"))

# Provider side: Kling always returns 5 second clips.
VIDEO_PROVIDER_CLIP_SEC = float(os.getenv("VIDEO_PROVIDER_CLIP_SEC", "5.0"))

# Trimming rules (seconds, on the ORIGINAL clip before speed-up).
# In manual mode we default to using the full 5s clip. In auto_ref mode we override per-scene.
VIDEO_TRIM_HEAD_SEC = float(os.getenv("VIDEO_TRIM_HEAD_SEC", "0.0"))
VIDEO_TRIM_KEEP_SEC = float(os.getenv("VIDEO_TRIM_KEEP_SEC", str(VIDEO_PROVIDER_CLIP_SEC)))

# Requirement: ALWAYS speed up x2 after generation to get snappier motion safely.
VIDEO_SPEED_FACTOR = float(os.getenv("VIDEO_SPEED_FACTOR", "2.0"))

VIDEO_CRF = int(os.getenv("VIDEO_CRF", "18"))

video_jobs: Dict[str, Dict[str, Any]] = {}
video_jobs_lock = threading.Lock()
video_executor = ThreadPoolExecutor(max_workers=2)

def _safe_filename_from_url(url: str) -> str:
    try:
        p = urlparse(url).path
        name = os.path.basename(p)
        return name or f"clip_{uuid.uuid4().hex}.png"
    except:
        return f"clip_{uuid.uuid4().hex}.png"

def _download_to_path(url: str, out_path: Path):
    """
    URL??http濡??쒖옉?섎㈃ ?ㅼ슫濡쒕뱶?섍퀬,
    / 濡??쒖옉?섎㈃ 濡쒖뺄 ?뚯씪??蹂듭궗?⑸땲??
    """
    # [?섏젙] 濡쒖뺄 ?뚯씪 寃쎈줈??寃쎌슦 (/outputs/... ??
    if url.startswith("/"):
        # 留??욎쓽 ?щ옒???쒓굅 (?덈?寃쎈줈 -> ?곷?寃쎈줈 蹂?? ?? /outputs/a.png -> outputs/a.png)
        local_path = url.lstrip("/")
        
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found on server: {local_path}")
            
        # ?⑥닚???뚯씪 蹂듭궗
        with open(local_path, "rb") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return

    # [湲곗〈] ?먭꺽 URL??寃쎌슦 (http://...)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)

def _run_ffmpeg(cmd: List[str]):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")

def _ffmpeg_trim_speed(in_path: Path, out_path: Path, start_sec: float, dur_sec: float, speed: float, fps: int):
    # trim -> reset timestamps -> speed up -> fps
    setpts_expr = f"(PTS-STARTPTS)/{speed}" if speed and abs(speed - 1.0) > 1e-6 else "(PTS-STARTPTS)"
    vf = f"trim=start={start_sec}:duration={dur_sec},setpts={setpts_expr},fps={fps}"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "10",          # [?섏젙] 18 -> 10 (珥덇퀬?붿쭏)
        "-preset", "veryslow", # [?섏젙] veryfast -> veryslow (?붿쭏 理쒖슦??
        str(out_path),
    ]
    _run_ffmpeg(cmd)

def _ffprobe_wh(path: Path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    data = json.loads(proc.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    return int(st.get("width") or 0), int(st.get("height") or 0)

def _ffmpeg_normalize_to(in_path: Path, out_path: Path, target_w: int, target_h: int, fps: int):
    # [FIX] 16:9 媛濡?-> 4:5 ?몃줈 媛뺤젣 以묒븰 ?щ∼ (Shorts/Reels ?ㅽ???
    # 蹂듭옟???⑤뵫/釉붾윭 濡쒖쭅???쒓굅?섍퀬, ?붾㈃??苑?梨꾩슫 ??以묒븰???먮Ⅴ??諛⑹떇 ?곸슜
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase," # 1. 鍮덇났媛??놁씠 苑?梨꾩슦?꾨줉 ?뺣? (鍮꾩쑉 ?좎?)
        f"crop={target_w}:{target_h}," # 2. 紐⑺몴 ?댁긽?꾨쭔??以묒븰???섎씪??        f"setsar=1," # 3. ?쎌? 鍮꾩쑉 1:1 媛뺤젣 (蹂묓빀 ?ㅻ쪟 諛⑹?)
        f"fps={fps}" # 4. ?꾨젅?꾨젅?댄듃 ?듭씪
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "10",          # [?섏젙] 18 -> 10 (珥덇퀬?붿쭏)
        "-preset", "veryslow", # [?섏젙] veryfast -> veryslow (?붿쭏 理쒖슦??
        str(out_path),
    ]
    _run_ffmpeg(cmd)
import io
import math

def _safe_extract_json(text: str) -> Dict[str, Any]:
    """Extract a JSON object from Gemini text safely."""
    if not text:
        return {}
    t = text.strip()
    if "```json" in t:
        t = t.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in t:
        t = t.split("```", 1)[1].split("```", 1)[0].strip() if t.count("```") >= 2 else t.split("```", 1)[0].strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    try:
        a = t.find("{")
        b = t.rfind("}")
        if a != -1 and b != -1 and b > a:
            obj = json.loads(t[a:b+1])
            return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    return {}

def _clip_url_to_image_bytes(url: str) -> bytes:
    """Supports data URI, local path (/...), and remote URL."""
    if url.startswith("data:image/"):
        try:
            _, encoded = url.split(",", 1)
            return base64.b64decode(encoded)
        except Exception:
            return base64.b64decode(url)
    if url.startswith("/"):
        local_path = url.lstrip("/")
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Image not found on server: {local_path}")
        return Path(local_path).read_bytes()
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content

def _find_static_image(prefix: str) -> Optional[Path]:
    """
    Finds static/{prefix}.* (png/jpg/jpeg/webp). Example: intro.png, outro.jpg
    """
    static_dir = Path("static")
    if not static_dir.exists():
        return None
    exts = ["png", "jpg", "jpeg", "webp"]
    cand = []
    for ext in exts:
        cand.extend(static_dir.glob(f"{prefix}*.{ext}"))
        cand.extend(static_dir.glob(f"{prefix.upper()}*.{ext}"))
        cand.extend(static_dir.glob(f"{prefix.capitalize()}*.{ext}"))
    cand = sorted(set(cand))
    return cand[0] if cand else None

def _ffmpeg_image_to_video(image_path: Path, out_path: Path, dur_sec: float, target_w: int, target_h: int, fps: int):
    """
    Turns a still image into a short video segment.
    [FIX] Removed fade in/out filters to ensure purely static image.
    """
    # [?섏젙] ?섏씠???④낵 ?쒓굅, ?댁긽??鍮꾩쑉留?留욎땄
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},setsar=1,fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-t", str(dur_sec),
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "10",          # [?섏젙] 18 -> 10
        "-preset", "veryslow", # [?섏젙] veryfast -> veryslow
        str(out_path),
    ]
    _run_ffmpeg(cmd)

# [NEW] 紐⑥뀡怨??댄럺?몃? 議고빀?섏뿬 ?꾨＼?꾪듃 ?앹꽦
def _kling_prompts_dynamic(motion: str, effect: str) -> Dict[str, str]:
    # 1. 湲곕낯 ?덉쭏 諛??좎? ?꾨＼?꾪듃
    base_keep = (
        "High quality interior video, photorealistic, 8k. "
        "Keep ALL furniture and layout exactly the same as the input image. "
        "No warping, no distortion. "
    )
    
    # 2. 紐⑥뀡 ?꾨＼?꾪듃 留ㅽ븨
    motion_map = {
        "static": "Static camera shot, extremely subtle movement.",
        "orbit_r_slow": "Slow orbit rotation to the right, keeping the subject centered, smooth movement.",
        "orbit_l_slow": "Slow orbit rotation to the left, keeping the subject centered, smooth movement.",
        "orbit_r_fast": "Fast orbit rotation to the right, dynamic camera movement.",
        "orbit_l_fast": "Fast orbit rotation to the left, dynamic camera movement.",
        "zoom_in_slow": "Slow camera dolly-in at eye-level. Move straight forward without shaking or walking bob. Smooth cinematic push.",
        "zoom_out_slow": "Slow camera dolly-out at eye-level. Move straight backward without shaking or walking bob. Smooth cinematic pull.",
        "zoom_in_fast": "Fast camera dolly-in at eye-level. Rapid straight movement towards the subject.",
        "zoom_out_fast": "Fast camera dolly-out at eye-level. Rapid straight movement away from the subject.",
    }
    
    # 3. ?댄럺???꾨＼?꾪듃 留ㅽ븨
    effect_map = {
        "none": "Natural lighting, static environment.",
        "sunlight": "Sunlight beams moving across the room, time-lapse shadow movement on the floor and furniture.",
        "lights_on": "Lighting transition: starts with lights off or dim, then lights turn on brightly. Cinematic illumination reveal.",
        "blinds": "Curtains or blinds moving gently in the wind near the window.",
        "plants": "Indoor plants and foliage swaying gently in a soft breeze.",
        "door_open": "A door, cabinet door, or glass door in the scene slowly opens.",
    }

    # ?꾨＼?꾪듃 議고빀
    p_motion = motion_map.get(motion, motion_map["static"])
    p_effect = effect_map.get(effect, effect_map["none"])
    
    final_prompt = f"{base_keep} {p_motion} {p_effect}"

    # ?ㅺ굅?곕툕 ?꾨＼?꾪듃
    neg = (
        "human, person, walking, shaking camera, shaky footage, "
        "changing furniture, melting objects, distorted geometry, "
        "text, watermark, logo, frame borders, low quality, cartoon"
    )
    
    return {"prompt": final_prompt, "negative_prompt": neg}

def _freepik_kling_create_task(image_b64: str, prompt: str, negative_prompt: str, duration: str, cfg_scale: float) -> str:
    if not FREEPIK_API_KEY:
        raise RuntimeError("FREEPIK_API_KEY (or MAGNIFIC_API_KEY) is not set.")
    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "duration": duration,
        "cfg_scale": cfg_scale,
        "image": image_b64
    }
    headers = {"x-freepik-api-key": FREEPIK_API_KEY, "Content-Type": "application/json"}
    with _video_sem:
        r = requests.post(KLING_ENDPOINT, headers=headers, json=payload, timeout=180)
    if r.status_code == 429:
        raise RuntimeError("Kling/Freepik rate limit hit (429). Try again later or lower VIDEO_MAX_CONCURRENCY.")
    if not r.ok:
        raise RuntimeError(f"Kling create failed ({r.status_code}): {r.text[:500]}")
    
    data = r.json()
    
    # ???붾쾭源? ?ㅼ젣 ?묐떟 援ъ“ 異쒕젰
    print(f"?뵇 [DEBUG] Kling API Response: {json.dumps(data, indent=2)}", flush=True)
    
    # ?щ윭 媛?ν븳 ?꾨뱶 ?쒕룄
    task_id = (
        data.get("task_id") or 
        data.get("id") or 
        data.get("data", {}).get("task_id") or 
        data.get("data", {}).get("id") or
        data.get("result", {}).get("task_id") or
        data.get("taskId")
    )
    
    if not task_id:
        print(f"??[ERROR] Could not find task_id. Full response keys: {list(data.keys())}", flush=True)
        raise RuntimeError(f"No task_id returned from Kling create. Response: {json.dumps(data)[:300]}")
    
    print(f"??[SUCCESS] Task created: {task_id}", flush=True)
    return task_id

import math # ?⑥닔 ?곷떒?대굹 ?뚯씪 理쒖긽?⑥뿉 import math ?꾩슂

def _freepik_kling_poll(task_id: str, job_id: str, clip_index: int, total_clips: int, timeout_sec: int = 600) -> str:
    headers = {"x-freepik-api-key": FREEPIK_API_KEY}
    start = time.time()
    poll_count = 0
    
    # [UX] 媛??대┰???좊떦??理쒕? 吏꾪뻾瑜?(?꾩껜??90%瑜??대┰ ?앹꽦??遺꾨같)
    # ?? ?대┰??1媛쒕㈃ 90%源뚯?, 2媛쒕㈃ 媛쒕떦 45%源뚯? ?좊떦
    clip_share_percent = 90 / max(1, total_clips)
    clip_start_percent = clip_index * clip_share_percent

    while True:
        if time.time() - start > timeout_sec:
            raise RuntimeError("Kling task timeout.")
        
        poll_count += 1
        
        # 1. API ?몄텧 (?ㅽ듃?뚰겕 ?먮윭 諛⑹뼱)
        try:
            with _video_sem:
                r = requests.get(f"{KLING_ENDPOINT}/{task_id}", headers=headers, timeout=60)
            
            if not r.ok:
                # 500 ?먮윭 ?깆? ?좎떆 ?湲????ъ떆??                if r.status_code >= 500:
                    print(f"?좑툘 [Server Warning] {r.status_code}. Retrying...", flush=True)
                    time.sleep(3)
                    continue
                raise RuntimeError(f"Kling status failed ({r.status_code}): {r.text[:300]}")
                
            st = r.json()
            
        except requests.exceptions.RequestException as e:
            print(f"?좑툘 [Network Warning] Polling failed temporarily: {e}. Retrying...", flush=True)
            time.sleep(3)
            continue

        # 2. [FIX] ?곗씠??援ъ“ 諛⑹뼱 濡쒖쭅 (AttributeError 'str' object 諛⑹?)
        data = st.get("data", {})
        status = "UNKNOWN"

        if isinstance(data, dict):
            status = data.get("status", "").upper()
        elif isinstance(st, dict):
             # data媛 ?녾굅??臾몄옄?댁씠硫?top-level?먯꽌 status ?뺤씤
            status = st.get("status", "").upper()
        
        # 3. [FIX] 吏꾪뻾瑜?濡쒖쭅 媛쒖꽑 (15% 硫덉땄 ?닿껐)
        # 濡쒓렇 ?⑥닔瑜??ъ슜?섏뿬 ?쒓컙??吏?좎닔濡?泥쒖쿇???ㅻⅤ吏留?100%???섏? ?딄쾶 ?ㅼ젙
        # poll_count媛 ?섏뼱?좎닔濡?clip_share_percent??95% ?섏?源뚯? ?먯쭊?곸쑝濡??묎렐
        simulated_progress = clip_share_percent * 0.95 * (1 - math.exp(-0.05 * poll_count))
        
        current_total_progress = int(clip_start_percent + simulated_progress)
        
        # 濡쒓렇 異쒕젰 (?ъ슜???덉떖??
        if poll_count <= 3 or poll_count % 5 == 0:
            print(f"?뵇 [Poll #{poll_count}] Clip {clip_index+1}/{total_clips} Status: {status} (Progress: {current_total_progress}%)", flush=True)

        with video_jobs_lock:
            if job_id in video_jobs:
                video_jobs[job_id]["progress"] = current_total_progress
                # 硫붿떆吏???ㅼ젣 ?쒕쾭 ?곹깭 ?ы븿
                video_jobs[job_id]["message"] = f"Generating clip {clip_index+1}/{total_clips}: {status}..."
        
        # 4. ?꾨즺 泥섎━
        if status in ("COMPLETED", "SUCCEEDED", "SUCCESS", "DONE"):
            print(f"??[COMPLETED] Clip {clip_index+1}/{total_clips}. Fetching URL...", flush=True)
            
            # generated ?꾨뱶 ?덉쟾 異붿텧
            generated = []
            if isinstance(data, dict):
                generated = data.get("generated", [])
            elif isinstance(st, dict):
                generated = st.get("generated", [])

            # ?꾨즺?섏뿀?붾뜲 URL??諛붾줈 ???⑤뒗 寃쎌슦 ?湲?            retry_count = 0
            while not generated and retry_count < 5:
                print(f"??[WAIT] Generated array empty, retrying... ({retry_count+1}/5)", flush=True)
                time.sleep(2)
                retry_count += 1
                
                with _video_sem:
                    r = requests.get(f"{KLING_ENDPOINT}/{task_id}", headers=headers, timeout=60)
                if r.ok:
                    st = r.json()
                    data = st.get("data", {})
                    if isinstance(data, dict):
                        generated = data.get("generated", [])
                    else:
                        generated = st.get("generated", [])

            # URL 李얘린
            url = None
            if generated and len(generated) > 0:
                first = generated[0]
                if isinstance(first, dict):
                    url = first.get("url") or first.get("video")
                elif isinstance(first, str):
                    url = first
            
            if not url and isinstance(data, dict):
                 url = data.get("video_url") or data.get("url") or data.get("video")
            
            if not url:
                url = st.get("result_url") or st.get("video_url")

            if url:
                print(f"??[SUCCESS] Found URL: {url[:60]}...", flush=True)
                return url
            
            print(f"??[ERROR] Completed but no URL. Response dump:", flush=True)
            print(json.dumps(st, indent=2), flush=True)
            raise RuntimeError("Kling completed but no result URL found.")
        
        if status in ("FAILED", "ERROR", "CANCELLED"):
            error_msg = "Unknown error"
            if isinstance(data, dict):
                error_msg = data.get("error") or data.get("message") or error_msg
            elif isinstance(data, str):
                error_msg = data
            elif isinstance(st, dict):
                 error_msg = st.get("error") or st.get("message") or error_msg
            
            raise RuntimeError(f"Kling task failed: {error_msg}")
        
        time.sleep(2)

def _image_url_to_b64(url: str) -> str:
    """
    ?대?吏 URL(?뱀? 濡쒖뺄 寃쎈줈)??諛쏆븘 Base64 臾몄옄?대줈 蹂?섑빀?덈떎.
    """
    # [?섏젙] 濡쒖뺄 ?뚯씪 寃쎈줈??寃쎌슦
    if url.startswith("/"):
        local_path = url.lstrip("/")
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found for b64 conversion: {local_path}")
            
        with open(local_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    # [湲곗〈] ?먭꺽 URL??寃쎌슦
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return base64.b64encode(r.content).decode("utf-8")

# -----------------------------------------------------------------------------
# [NEW] ?⑥씪 ?대┰ 泥섎━ ?⑥닔 (蹂묐젹 ?ㅽ뻾??
# -----------------------------------------------------------------------------
# =========================================================
# [NEW] 2-Step Video Logic (Source Gen -> Final Compile)
# =========================================================

# --- 1. Request Models (?곗씠??紐⑤뜽 ?뺤쓽) ---
class SourceItem(BaseModel):
    url: str
    motion: str = "static"
    effect: str = "none"

class SourceGenRequest(BaseModel):
    items: List[SourceItem]
    cfg_scale: float = 0.5

class CompileClip(BaseModel):
    video_url: str
    speed: float = 1.0
    trim_start: float = 0.0
    trim_end: float = 5.0

class CompileRequest(BaseModel):
    clips: List[CompileClip]
    include_intro_outro: bool = False
    intro_url: Optional[str] = None
    outro_url: Optional[str] = None

def _generate_raw_only(idx, item, job_id, out_dir, cfg_scale):
    """
    Step 1: ?뚯뒪 ?앹꽦 濡쒖쭅
    - Static & No Effect: FFmpeg濡?利됱떆 蹂??(Fast, Free)
    - Motion or Effect: Kling AI ?몄텧 (Slow, Cost)
    """
    filename = f"source_{job_id}_{idx}.mp4"
    out_path = out_dir / filename
    
    # [理쒖쟻?? ?吏곸엫???녾퀬, ?④낵???놁쑝硫?-> 洹몃깷 ?대?吏 5珥??곸긽?쇰줈 蹂??(Kling X)
    if item.motion == "static" and item.effect == "none":
        print(f"?? [Clip {idx}] Static detected. Skipping Kling (Fast generation).", flush=True)
        temp_img = out_dir / f"temp_src_{job_id}_{idx}.png"
        try:
            # 1. ?대?吏 ?ㅼ슫濡쒕뱶
            _download_to_path(item.url, temp_img)
            
            # [?섏젙] 1080, 1920 (?몃줈) ?뚮씪誘명꽣 ?뺤씤
            _ffmpeg_image_to_video(
                temp_img, out_path, 
                5.0, 
                1080, 1920, # <--- ?ш린媛 1080, 1920 ?댁뼱????                VIDEO_TARGET_FPS
            )
            return out_path
        except Exception as e:
            print(f"Static Gen Error: {e}")
            raise e
        finally:
            if temp_img.exists(): temp_img.unlink()

    # ---------------------------------------------------------
    # 洹???(紐⑥뀡?대굹 ?댄럺?멸? ?덈뒗 寃쎌슦) -> Kling ?몄텧
    # ---------------------------------------------------------
    print(f"?렏 [Clip {idx}] Kling AI Generating... ({item.motion}/{item.effect})", flush=True)
    
    prompts = _kling_prompts_dynamic(item.motion, item.effect)
    img_b64 = _image_url_to_b64(item.url)
    
    # 5珥??앹꽦 ?붿껌
    task_id = _freepik_kling_create_task(
        img_b64, prompts["prompt"], prompts["negative_prompt"], 
        "5", cfg_scale
    )
    
    # ?대쭅 ?湲?    video_url = _freepik_kling_poll(task_id, job_id, idx, 1)
    
    # ?ㅼ슫濡쒕뱶
    _download_to_path(video_url, out_path)
    
    return out_path

def _run_source_generation(job_id: str, items: List[SourceItem], cfg_scale: float):
    try:
        with video_jobs_lock:
            video_jobs[job_id] = {"status": "RUNNING", "message": "Initializing...", "progress": 0, "results": []}

        out_dir = Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)
        
        total_steps = len(items)
        results_map = [None] * total_steps # ?쒖꽌 蹂댁옣??        
        # 蹂묐젹 ?ㅽ뻾 (理쒕? 5媛??숈떆)
        with ThreadPoolExecutor(max_workers=VIDEO_MAX_CONCURRENCY) as executor:
            future_map = {}
            for i, item in enumerate(items):
                future = executor.submit(_generate_raw_only, i, item, job_id, out_dir, cfg_scale)
                future_map[future] = i

            completed_count = 0
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    path = future.result() 
                    if path:
                        # ?뱀뿉???묎렐 媛?ν븳 寃쎈줈濡????                        results_map[idx] = f"/outputs/{path.name}"
                except Exception as e:
                    print(f"Clip {idx} failed: {e}")
                    results_map[idx] = None # ?ㅽ뙣 ??None
                
                completed_count += 1
                # 吏꾪뻾瑜??낅뜲?댄듃
                with video_jobs_lock:
                    video_jobs[job_id]["progress"] = int((completed_count / total_steps) * 100)
                    video_jobs[job_id]["message"] = f"Generated {completed_count}/{total_steps} clips"

        # ?꾨즺
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "COMPLETED"
            video_jobs[job_id]["results"] = results_map # 寃곌낵 由ъ뒪??諛섑솚
            video_jobs[job_id]["message"] = "Source generation complete."

    except Exception as e:
        print(f"Source Gen Critical Error: {e}")
        traceback.print_exc()
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "FAILED"
            video_jobs[job_id]["error"] = str(e)

# --- 3. Step 2: Final Compile (?먮Ⅴ湲?諛곗냽/蹂묓빀) ---
def _run_final_compile(job_id: str, req: CompileRequest):
    try:
        with video_jobs_lock:
            video_jobs[job_id] = {"status": "RUNNING", "message": "Compiling...", "progress": 0}
            
        out_dir = Path("outputs")
        processed_paths = []
        
        total_clips = len(req.clips)
        
        # 1. 媛??대┰ 媛怨?(Trim -> Speed -> Resize)
        for i, clip in enumerate(req.clips):
            if not clip.video_url: continue
            
            # ?먮낯 ?뚯씪 ?뺣낫 (濡쒖뺄???놁쑝硫??ㅼ슫濡쒕뱶)
            src_name = _safe_filename_from_url(clip.video_url)
            local_src = out_dir / src_name
            if not local_src.exists():
                _download_to_path(clip.video_url, local_src)
            
            final_path = out_dir / f"proc_{job_id}_{i}.mp4"
            
            # ?뚮씪誘명꽣 怨꾩궛
            t_start = max(0.0, clip.trim_start)
            t_end = min(5.0, clip.trim_end)
            if t_end <= t_start: t_end = 5.0
            
            dur = t_end - t_start
            # ?띾룄 ?덉쟾?μ튂 (0?대㈃ 1.0?쇰줈)
            speed = clip.speed if clip.speed > 0.1 else 1.0
            
            # FFmpeg ?꾪꽣 援ъ꽦:
            # 1. trim: 援ш컙 ?먮Ⅴ湲?            # 2. setpts: ?띾룄 議곗젅 ((PTS-STARTPTS)/speed)
            # 3. scale/crop: ?댁긽??媛뺤젣 ?듭씪 (1080x1920 ??湲곗〈 ?ㅼ젙 ?곕쫫)
            # 4. setsar=1: ?쎌? 鍮꾩쑉 珥덇린??(蹂묓빀 ?ㅻ쪟 諛⑹?)
            setpts = f"(PTS-STARTPTS)/{speed}"
            
# [?섏젙] 1080x1920 ?몃줈??9:16) 媛뺤젣 ?곸슜
            vf = (
                f"trim=start={t_start}:duration={dur},setpts={setpts},"
                f"scale=1080:1920:force_original_aspect_ratio=increase," # 9:16 鍮꾩쑉濡??섎━怨?                f"crop=1080:1920,setsar=1,fps={VIDEO_TARGET_FPS}"       # 以묒븰 ?щ∼
            )
            
            cmd = [
                "ffmpeg", "-y", "-i", str(local_src),
                "-vf", vf, "-an", 
                "-c:v", "libx264", "-pix_fmt", "yuv420p", 
                "-preset", "veryslow", # [?섏젙] veryfast -> veryslow
                "-crf", "10",          # [?섏젙] 18 -> 10
                str(final_path)
            ]
            _run_ffmpeg(cmd)
            processed_paths.append(final_path)
            
            # 吏꾪뻾瑜?(0~80%)
            with video_jobs_lock:
                video_jobs[job_id]["progress"] = int(((i + 1) / total_clips) * 80)

        # 2. 蹂묓빀 (Concat)
        if not processed_paths: raise RuntimeError("No clips to merge")
        
        list_file = out_dir / f"list_{job_id}.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in processed_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")
        
        final_out = out_dir / f"final_{job_id}.mp4"
        # Concat ?ㅽ뻾
        _run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(final_out)])
        
        result_url = f"/outputs/{final_out.name}"
        
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "COMPLETED"
            video_jobs[job_id]["result_url"] = result_url
            video_jobs[job_id]["progress"] = 100
            
    except Exception as e:
        print(f"Compile Error: {e}")
        traceback.print_exc()
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "FAILED"
            video_jobs[job_id]["error"] = str(e)

# --- 4. API Endpoints (New) ---

@app.post("/video-mvp/generate-sources")
async def api_generate_sources(req: SourceGenRequest):
    job_id = uuid.uuid4().hex
    with video_jobs_lock:
        video_jobs[job_id] = {"status": "QUEUED", "progress": 0}
    
    # 諛깃렇?쇱슫???ㅻ젅?쒕줈 ?ㅽ뻾
    threading.Thread(target=_run_source_generation, args=(job_id, req.items, req.cfg_scale)).start()
    return {"job_id": job_id}

@app.post("/video-mvp/compile")
async def api_compile_final(req: CompileRequest):
    job_id = uuid.uuid4().hex
    with video_jobs_lock:
        video_jobs[job_id] = {"status": "QUEUED", "progress": 0}
        
    threading.Thread(target=_run_final_compile, args=(job_id, req)).start()
    return {"job_id": job_id}

@app.get("/video-mvp/status/{job_id}")
async def video_mvp_status(job_id: str):
    with video_jobs_lock:
        st = video_jobs.get(job_id)
    if not st:
        return JSONResponse({"status": "NOT_FOUND", "message": "Job not found"}, status_code=404)
    return st


# --- Auto Cleanup System ---
RETENTION_SECONDS = 7 * 24 * 60 * 60  # 7 days 
CLEANUP_INTERVAL = 600

def auto_cleanup_task():
    while True:
        try:
            now = time.time()
            
            # 1. ?뚯씪 ?뺣━ (湲곗〈 濡쒖쭅 ?좎?)
            deleted_count = 0
            folder = "outputs"
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    file_path = os.path.join(folder, filename)
                    if os.path.isfile(file_path) and filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.mp4')):
                        file_age = now - os.path.getmtime(file_path)
                        if file_age > RETENTION_SECONDS:
                            try:
                                os.remove(file_path)
                                deleted_count += 1
                            except Exception: pass
            
            # 2. [FIX] 硫붾え由??뺣━: ?꾨즺?섏뿀嫄곕굹 ?ㅻ옒??Job ID ??젣 (硫붾え由??꾩닔 諛⑹?)
            # Job ?앹꽦 ??24?쒓컙(86400珥? 吏??湲곕줉? ??젣
            JOB_RETENTION = 86400 
            with video_jobs_lock:
                # ?뺤뀛?덈━瑜??쒗쉶?섎ŉ ??젣?댁빞 ?섎?濡???由ъ뒪??蹂듭궗 ?ъ슜
                for jid in list(video_jobs.keys()):
                    # progress媛 100?닿굅??failed???곹깭?먯꽌 ?ㅻ옒??寃? ?뱀? 洹몃깷 ?덈Т ?ㅻ옒??寃???젣
                    # ?ш린?쒕뒗 ?⑥닚?섍쾶 ?앹꽦 ?쒓컙??蹂꾨룄 異붿쟻 ?덊븯誘濡? ?쇰떒 100% ?꾨즺??嫄?諛붾줈 吏?곗? ?딄퀬(?ㅼ슫濡쒕뱶 ?꾪빐),
                    # 由ъ뒪??愿由??뺤콉???꾩슂??
                    # 媛꾨떒?섍쾶: video_jobs??timestamp ?꾨뱶瑜?異붽??섎뒗 寃껋씠 ?뺤꽍?대굹,
                    # ?꾩옱 援ъ“??'?덈Т 留롮븘吏硫?媛뺤젣 ?뺣━' 諛⑹떇?쇰줈 援ы쁽.
                    if len(video_jobs) > 1000: # ?뱀떆 1000媛쒓? ?섏뼱媛硫?                        video_jobs.pop(jid, None) # ?욎뿉?쒕????섎굹 吏? (Python 3.7+ ?뺤뀛?덈━???쎌엯 ?쒖꽌 ?좎??섎?濡?媛???ㅻ옒??寃???젣??
            
            if deleted_count > 0:
                print(f"??[System] Cleaned up {deleted_count} old files.", flush=True)
                
        except Exception as e:
            print(f"!! [Cleanup Error] {e}", flush=True)
        time.sleep(CLEANUP_INTERVAL)

import threading
import subprocess
from urllib.parse import urlparse
from pathlib import Path
cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
cleanup_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, log_level="info")
