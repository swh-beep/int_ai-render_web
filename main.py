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
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel
import gc
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------
# 1. 환경 설정 및 초기화
# ---------------------------------------------------------
load_dotenv()

MODEL_NAME = 'gemini-3-pro-image-preview'       # 절대 변경 금지
ANALYSIS_MODEL_NAME = 'gemini-3-flash-preview'  # 절대 변경 금지
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

print(f"✅ 로드된 나노바나나 API 키 개수: {len(API_KEY_POOL)}개", flush=True)

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
            print("🔄 [System] 모든 키가 락 상태. 5초 쿨다운 후 초기화.", flush=True)
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
                print(f"📉 [Lock] Key(...{masked_key}) 할당량 초과. (잠시 휴식)", flush=True)
                QUOTA_EXCEEDED_KEYS.add(current_key)
                time.sleep(2 + attempt) 
            else:
                print(f"⚠️ [Error] Key(...{masked_key}) 에러: {error_msg}", flush=True)
                time.sleep(1)

    print("❌ [Fatal] 모든 키 시도 실패.", flush=True)
    return None

def standardize_image(image_path, output_path=None, keep_ratio=False):
    try:
        if output_path is None: output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB': img = img.convert('RGB')

            width, height = img.size
            
            # [수정] 가로/세로 비율 판단 및 타겟 해상도 설정
            if width >= height:
                # Landscape (가로형) -> 16:9
                target_size = (1920, 1080)
                target_ratio = 16 / 9
            else:
                # Portrait (세로형) -> 4:5 (인테리어 표준 세로 비율)
                target_size = (1080, 1350)
                target_ratio = 4 / 5

            if not keep_ratio:
                current_ratio = width / height

                if current_ratio > target_ratio:
                    # 이미지가 더 납작한 경우 (양옆 자름)
                    new_width = int(height * target_ratio)
                    offset = (width - new_width) // 2
                    img = img.crop((offset, 0, offset + new_width, height))
                else:
                    # 이미지가 더 홀쭉한 경우 (위아래 자름)
                    new_height = int(width / target_ratio)
                    offset = (height - new_height) // 2
                    img = img.crop((0, offset, width, offset + new_height))

                # 최종 리사이즈 (LANCZOS 필터 사용)
                img = img.resize(target_size, Image.Resampling.LANCZOS)

            base, _ = os.path.splitext(output_path)
            new_output_path = f"{base}.png"
            img.save(new_output_path, "PNG")
            return new_output_path
    except Exception as e:
        print(f"!! 표준화 실패: {e}", flush=True)
        return image_path


# ---------------------------------------------------------
# [NEW] Output Aspect Ratio Enforcement
# - Gemini가 무드보드 비율/레이아웃을 따라가거나,
#   하단에 흰 배경(카탈로그/텍스트) 영역을 붙여서 내보내는 케이스를
#   "방 사진 캔버스" 기준으로 강제 보정합니다.
# ---------------------------------------------------------

def _is_bottom_strip_mostly_white(img: Image.Image, strip_ratio: float = 0.22, white_thresh: int = 245) -> bool:
    """하단 strip이 '거의 흰색'인지 휴리스틱으로 판단합니다.

    - 무드보드/인벤토리 시트가 하단에 붙는 경우 흰 배경이 대량 포함되는 패턴이 많아서
      landscape 강제 크롭 시 '위쪽 고정(top anchor)' 여부를 결정하는 데 사용합니다.
    """
    try:
        w, h = img.size
        if w <= 0 or h <= 0:
            return False

        strip_h = max(1, int(h * strip_ratio))
        y0 = max(0, h - strip_h)
        strip = img.crop((0, y0, w, h))

        # 계산 비용을 낮추기 위해 축소 후 판단
        strip = strip.resize((256, max(1, int(256 * strip_ratio))), Image.Resampling.BILINEAR)
        gray = strip.convert('L')
        pixels = list(gray.getdata())
        if not pixels:
            return False

        white_count = sum(1 for p in pixels if p >= white_thresh)
        white_ratio = white_count / len(pixels)

        # 35% 이상이 순백(근처)이면 "하단이 흰 시트"일 확률이 높다고 가정
        return white_ratio >= 0.35
    except Exception:
        return False


def standardize_image_to_reference_canvas(
    image_path: str,
    reference_path: str,
    output_path: Optional[str] = None,
) -> str:
    """생성 결과물을 'reference 이미지(=빈 방 캔버스)'의 비율/해상도로 강제 통일합니다.

    - 핵심: 무드보드가 세로여도 최종 결과는 방 사진 캔버스(16:9 또는 4:5)로 강제.
    - 추가: 결과 이미지가 세로로 튀면서 하단에 흰 인벤토리 영역이 붙는 케이스를
            top-anchor 크롭으로 잘라내는 휴리스틱을 적용.
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

            # 이미 목표 캔버스와 동일하면 그대로 PNG로만 저장 (안전)
            if abs(current_ratio - target_ratio) < 1e-3 and (w, h) == (ref_w, ref_h):
                base, _ = os.path.splitext(output_path or image_path)
                out_path = f"{base}.png"
                img.save(out_path, "PNG")
                return out_path

            if current_ratio > target_ratio:
                # 너무 넓음: 좌우 크롭
                new_w = int(h * target_ratio)
                x0 = max(0, (w - new_w) // 2)
                img = img.crop((x0, 0, x0 + new_w, h))
            else:
                # 너무 높음: 상하 크롭
                new_h = int(w / target_ratio)
                new_h = min(new_h, h)

                # 하단에 흰 시트가 붙는 패턴이면 위쪽 기준으로 크롭 (하단 제거)
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
            f"Describe the visual traits of this '{label}' for a 3D artist.\n"
            "Focus ONLY on:\n"
            "1. Material (e.g., leather, wood, fabric type)\n"
            "2. Color (exact shade)\n"
            "3. Shape & Structure (legs, armrests, silhouette)\n\n"
            
            "<CRITICAL: NEGATIVE CONSTRAINTS>\n"
            "1. **IGNORE BACKGROUND:** Do NOT mention 'white background', 'studio shot', or 'grey backdrop'. Act as if the object is floating.\n"
            "2. **IGNORE TEXT:** Do NOT read or mention any dimensions (e.g., '2400mm') or watermarks visible in the image.\n"
            "3. **NO LAYOUT INFO:** Do not describe it as 'collage' or 'grid'.\n"
            "OUTPUT FORMAT: Concise visual description within 50-80 words."
        )
        response = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [prompt, cropped_img], {'timeout': 30}, {})
        
        if response and response.text:
            return {"label": label, "description": response.text.strip()}
            
    except Exception as e:
        print(f"!! Crop Analysis Failed for {label}: {e}", flush=True)
    
    return {"label": label, "description": f"A high quality {label}."}

# -----------------------------------------------------------------------------
# Generation Logic
# -----------------------------------------------------------------------------

def generate_empty_room(image_path, unique_id, start_time, stage_name="Stage 1"):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    print(f"\n--- [{stage_name}] 빈 방 생성 시작 ({MODEL_NAME}) ---", flush=True)
    
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
                        print(f">> [성공] 빈 방 이미지 생성됨! ({try_count+1}회차)", flush=True)
                        timestamp = int(time.time())
                        filename = f"empty_{timestamp}_{unique_id}.png"
                        path = os.path.join("outputs", filename)
                        with open(path, 'wb') as f: f.write(part.inline_data.data)
                        # [FIX] Stage 1 결과도 입력 캔버스(원본 방 사진) 비율/해상도로 강제 통일
                        return standardize_image_to_reference_canvas(path, image_path)
            else:
                print(f"⚠️ [Blocked] 안전 필터 차단", flush=True)
        print(f"⚠️ [Retry] 시도 {try_count+1} 실패. 재시도...", flush=True)

    print(">> [실패] 빈 방 생성 불가. 원본 사용.", flush=True)
    return image_path

# [수정] 원본 프롬프트 유지 + 비율 자동 감지 + 텍스트/여백 금지 + 무드보드 비율 무시
def generate_furnished_room(room_path, style_prompt, ref_path, unique_id, furniture_specs=None, start_time=0):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return None
    try:
        room_img = Image.open(room_path)
        
        # [NEW] 이미지 비율 계산 (가로형/세로형 판단)
        width, height = room_img.size
        is_portrait = height > width
        ratio_instruction = "PORTRAIT (4:5 Ratio)" if is_portrait else "LANDSCAPE (16:9 Ratio)"
        
        system_instruction = "You are an expert interior designer AI."
        
        # [수정] 스펙 데이터 (레이아웃 무시 경고 포함)
        specs_context = ""
        if furniture_specs:
            specs_context = (
                "\n<REFERENCE MATERIAL PALETTE (READ ONLY)>\n"
                "The following list describes the MATERIALS and COLORS.\n"
                "**WARNING:** Do NOT copy the text/dimensions/layout from the reference. Use ONLY materials.\n"
                f"{furniture_specs}\n"
                "--------------------------------------------------\n"
            )

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

            "<CRITICAL: DIMENSIONAL TEXT ADHERENCE>\n"
            "1. **OCR & CONSTRAINTS:** Actively SCAN the 'Style Reference' image for any text indicating dimensions (e.g., '2400mm', 'W:200cm', '3-seater', '1800x900').\n"
            "2. **SCALE ENFORCEMENT:** If dimensions are present, YOU MUST calibrate the size of the generated furniture to match these specific measurements relative to the room's perspective.\n"
            "3. **LOGIC CHECK:** Do not generate furniture that contradicts the text (e.g., if text says '1-person chair', do not generate a '3-person sofa').\n\n"

            "<CRITICAL: WINDOW LIGHT MUST BE ABUNDANT (PRIORITY #1)>\n"
"1. **ABUNDANT WINDOW LIGHT:** The scene MUST be strongly illuminated by abundant daylight coming from the window.\n"
"2. **EXPOSURE RULE:** Bright and airy (not dark), while preserving highlight detail (no blown-out whites).\n"
"3. **LIGHT DIRECTION:** Clearly visible light direction from the window; cast soft but present shadows across the floor.\n"
"4. **NO DIM ROOM:** Do NOT generate a dim, underexposed, moody, or nighttime look.\n"
"5. **WHITE BALANCE:** Neutral/cool daylight white balance (around 5200–5600K). **NO warm/yellow cast.**\n\n"
"<CRITICAL: PHOTOREALISTIC LIGHTING INTEGRATION>\n"
"1. **DAYLIGHT DOMINANT:** Daylight from the window is the KEY light. Simulate how neutral daylight bounces and interacts with furniture.\n"
"2. **ARTIFICIAL LIGHTS RULE:** Do NOT add warm/tungsten lighting. If there are existing fixtures, keep them neutral white (5000–5600K) and subtle. If turning lights on would introduce a yellow tint, keep them OFF.\n"
"3. **SHADOW PHYSICS:** Generate soft shadows that match the direction and intensity of the sunlight entering the room.\n"
"4. **ATMOSPHERE:** Sun-filled, fresh, high-end interior photography 느낌 — but keep colors neutral and clean (no sepia).\n"
"OUTPUT RULE: Return the original room image with furniture added, perfectly blended with neutral daylight.\n"

        )

        # [조립] 비율 고정 및 '무드보드 비율 무시' 명령 추가 (세로 무드보드 문제 해결)
        prompt = (
            "ACT AS: Professional Interior Photographer.\n"
            f"{specs_context}\n" 
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
                    # [FIX] 무드보드 비율/레이아웃 영향을 받더라도 최종 결과를 "방 캔버스"로 강제 통일
                    return standardize_image_to_reference_canvas(path, room_path)
        return None
    except Exception as e:
        print(f"!! Stage 2 에러: {e}", flush=True)
        return None

def call_magnific_api(image_path, unique_id, start_time):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: 
        return image_path
    
    print(f"\n--- [Stage 4] 업스케일링 시도 (Key: {MAGNIFIC_API_KEY[:5]}...) ---", flush=True)
    
    if not MAGNIFIC_API_KEY or "your_" in MAGNIFIC_API_KEY:
         print(">> [SKIP] API 키가 없습니다. 원본 반환.", flush=True)
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
            "hdr": 1,
            "resemblance": 10,
            "fractality": 1,
            "prompt": (
                "Professional interior photography, architectural digest style, "
                "shot on Phase One XF IQ4, 100mm lens, ISO 100, f/8, "
                "natural white daylight coming from window, sharp shadows, subtle film grain, "
                "hyper-realistic material textures, raw photo, 8k resolution, "
                "imperfect details, dust particles in air, "
                "--no 3d render, cgi, painting, drawing, cartoon, anime, illustration, "
                "plastic look, oversaturated, watermark, text, blur, distorted, "
                "smudge, bad geometry, mutated, glossy skin, artificial light"
            )
        }
        headers = {
            "x-freepik-api-key": MAGNIFIC_API_KEY, 
            "Content-Type": "application/json"
        }
        
        res = requests.post(MAGNIFIC_ENDPOINT, json=payload, headers=headers)
        
        if res.status_code != 200:
            print(f"!! [API 오류] Status: {res.status_code}, Msg: {res.text}", flush=True)
            return image_path

        data = res.json()
        
        if "data" not in data:
            return image_path

        if "task_id" in data["data"]:
            task_id = data["data"]["task_id"]
            print(f">> 작업 예약됨 (ID: {task_id})...", end="", flush=True)
            
            while time.time() - start_time < TOTAL_TIMEOUT_LIMIT:
                time.sleep(2)
                print(".", end="", flush=True)
                
                check = requests.get(f"{MAGNIFIC_ENDPOINT}/{task_id}", headers=headers)
                if check.status_code == 200:
                    status_data = check.json().get("data", {})
                    status = status_data.get("status")
                    
                    if status == "COMPLETED":
                        print(" 완료!", flush=True)
                        gen_list = status_data.get("generated", [])
                        if gen_list and len(gen_list) > 0:
                            return download_image(gen_list[0], unique_id) or image_path
                    elif status == "FAILED": 
                        print(f" 실패.", flush=True)
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


# =========================
# Video Studio (separate page)
# =========================
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

    valid_indices = []
    try:
        for f in os.listdir(base_dir):
            if f.startswith(prefix) and f.endswith(".png"):
                try:
                    num_part = f.replace(prefix, "").replace(".png", "")
                    if num_part.isdigit():
                        valid_indices.append(int(num_part))
                except: continue
        
        valid_indices.sort()
        return valid_indices
    except Exception as e:
        print(f"Thumbnail Scan Error: {e}")
        return []

# --- 메인 렌더링 엔드포인트 ---
@app.post("/render")
def render_room(
    file: UploadFile = File(...), 
    room: str = Form(...), 
    style: str = Form(...), 
    variant: str = Form(...),
    moodboard: UploadFile = File(None) 
):
    try:
        unique_id = uuid.uuid4().hex[:8]
        print(f"\n=== 요청 시작 [{unique_id}] (Integrated Analysis Mode) ===", flush=True)
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
            assets_dir = os.path.join("assets", safe_room, safe_style)
            if os.path.exists(assets_dir):
                files = sorted(os.listdir(assets_dir))
                found = False
                import re 
                pattern = rf"(?:^|[^0-9]){re.escape(variant)}(?:[^0-9]|$)"
                for f in files:
                    if re.search(pattern, f):
                        ref_path = os.path.join(assets_dir, f)
                        mb_url = f"/assets/{safe_room}/{safe_style}/{f}"
                        found = True
                        break
                if not found and len(files) > 0:
                    ref_path = os.path.join(assets_dir, files[0])
                    mb_url = f"/assets/{safe_room}/{safe_style}/{files[0]}"
        
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
        print(f"\n🚀 [Stage 2] 5장 동시 생성 시작 (Specs Injection)!", flush=True)

        def process_one_variant(index):
            sub_id = f"{unique_id}_v{index+1}"
            try:
                current_style_prompt = STYLES.get(style, "Custom Moodboard Style")
                res = generate_furnished_room(step1_img, current_style_prompt, ref_path, sub_id, furniture_specs=furniture_specs_text, start_time=start_time)
                if res: return f"/outputs/{os.path.basename(res)}"
            except Exception as e: print(f"   ❌ [Variation {index+1}] 에러: {e}", flush=True)
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
        print(f"\n🔥🔥🔥 [SERVER CRASH] {e}", flush=True)
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

        # [업그레이드]
        # 1) 가구방 업스케일을 먼저 시작해두고(백그라운드 스레드),
        # 2) 그 동안 빈방 생성 -> 빈방 업스케일 시작
        # => 체감 대기시간을 줄입니다.
        final_empty_path = ""
        final_furnished_path = ""

        # 업스케일링도 5-worker로 병렬 처리 (동시 요청 처리 여유)
        with ThreadPoolExecutor(max_workers=5) as executor:
            print(">> [Step 1] Upscaling Furnished in parallel...", flush=True)
            future_furnished = executor.submit(call_magnific_api, local_path, unique_id + "_upscale_furnished", start_time)

            print(">> [Step 2] Creating matched Empty Room...", flush=True)
            empty_room_path = generate_empty_room(local_path, unique_id + "_final_empty", start_time, stage_name="Finalize: Empty Gen")

            print(">> [Step 3] Upscaling Empty Room...", flush=True)
            future_empty = executor.submit(call_magnific_api, empty_room_path, unique_id + "_upscale_empty", start_time)

            # 결과 대기
            final_furnished_path = future_furnished.result()
            final_empty_path = future_empty.result()

        return JSONResponse(content={
            "upscaled_furnished": f"/outputs/{os.path.basename(final_furnished_path)}",
            "upscaled_empty": f"/outputs/{os.path.basename(final_empty_path)}",
            "message": "Success"
        })

    except Exception as e:
        print(f"🔥🔥🔥 [Finalize Error] {e}")
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
    # [수정 1] 좌측 공간 강조 (카메라 이동 X, 프레임 집중 O)
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

    # [수정 2] 우측 공간 강조
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

@app.post("/generate-details")
def generate_details_endpoint(req: DetailRequest):
    try:
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "Original image not found"}, status_code=404)

        unique_id = uuid.uuid4().hex[:6]
        print(f"\n=== [Detail View] 요청 시작 ({unique_id}) - Smart Cache Mode ===", flush=True)

        analyzed_items = []
        
        if req.furniture_data and len(req.furniture_data) > 0:
            print(">> [Smart Cache] Using pre-analyzed furniture data!", flush=True)
            analyzed_items = req.furniture_data
        else:
            print(">> [Smart Cache] No cached data found. Analyzing now...", flush=True)
            if req.moodboard_url:
                if req.moodboard_url.startswith("/assets/"):
                    rel_path = req.moodboard_url.lstrip("/")
                    mb_path = os.path.join(*rel_path.split("/"))
                else:
                    mb_filename = os.path.basename(req.moodboard_url)
                    mb_path = os.path.join("outputs", mb_filename)

                if os.path.exists(mb_path):
                    detected_items = detect_furniture_boxes(mb_path)
                    print(f">> [Deep Analysis] Analyzing {len(detected_items)} items...", flush=True)
                    with ThreadPoolExecutor(max_workers=5) as executor:
                        futures = [executor.submit(analyze_cropped_item, mb_path, item) for item in detected_items]
                        analyzed_items = [f.result() for f in futures]
                else:
                    print(f"!! Moodboard file not found at {mb_path}, using default.", flush=True)
            else:
                 print("!! No Moodboard URL provided, using default list.", flush=True)
                 analyzed_items = [{"label": "Sofa"}, {"label": "Chair"}, {"label": "Table"}]
        
        dynamic_styles = construct_dynamic_styles(analyzed_items)
        
        generated_results = []
        print(f"🚀 Generating {len(dynamic_styles)} Dynamic Shots...", flush=True)
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i, style in enumerate(dynamic_styles):
                futures.append((i, executor.submit(generate_detail_view, local_path, style, unique_id, i+1)))
            
            for i, future in futures:
                res = future.result()
                if res: 
                    generated_results.append({"index": i, "url": res})
                
        print(f"=== [Detail View] 완료: {len(generated_results)}장 생성됨 ===", flush=True)
        
        if not generated_results:
            return JSONResponse(content={"error": "Failed to generate images"}, status_code=500)

        return JSONResponse(content={
            "details": generated_results,
            "message": "Detail views generated successfully"
        })

    except Exception as e:
        print(f"🔥🔥🔥 [Detail Error] {e}")
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
        print(f"🔥🔥🔥 [Moodboard Gen Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

def generate_single_room_from_plan(plan_img, ref_images, unique_id, index):
    try:
        system_instruction = "You are an expert architectural visualizer."
        
        prompt = (
            "TASK: Reconstruct an empty room strictly based on the Floor Plan's geometry, applying materials from Reference Photos.\n\n"
            
            "INPUTS:\n"
            "- Plan: **THE ONLY TRUTH FOR GEOMETRY.** (Black lines = Walls. White spaces = Openings).\n"
            f"- Ref Photos ({len(ref_images)} images): **TEXTURE PALETTE ONLY.** (Do NOT copy the room shape from these photos.)\n\n"

            "<CRITICAL: GEOMETRY ENFORCEMENT - IGNORE REFERENCE SHAPE>\n"
            "1. **THE PHOTOS ARE A LIE:** The Reference Photos show a DIFFERENT room shape (flat walls). **IGNORE THE SHAPE IN THE PHOTOS.**\n"
            "2. **READ THE PLAN'S KINKS:** Look at the Floor Plan. It is NOT a simple rectangle.\n"
            "   - **Right Wall:** Does the line step back? Is there a niche or a pillar? **Render that 90-degree corner/jog visibly.** Create a shadow in that corner to show depth.\n"
            "   - **Left Wall:** If there is a door arc, cut a hole for the door frame. Do not make it a solid wall.\n"
            "3. **ASYMMETRY IS KEY:** The left wall and right wall are different. Do not make them symmetrical just because it looks nice. Follow the ink lines of the plan.\n\n"

            "<CRITICAL: MATERIAL MAPPING (TEXTURE ONLY)>\n"
            "1. **Floor:** Extract the wood flooring texture from the photos and apply it to your new 3D geometry.\n"
            "2. **Ceiling:** Copy the 'Well Ceiling' (cove lighting) design from the photo. Keep the proportions relative to the room width.\n"
            "3. **Walls:** Apply the same wallpaper color and baseboard molding from the photos onto the *Plan's* walls.\n"
            "4. **Accents:** If the photo shows wood paneling, apply it ONLY to the walls that match the plan's solid sections.\n\n"

            "<CRITICAL: EMPTY ROOM & DOORS>\n"
            "1. **NO FURNITURE:** Remove all sofa, TV, rug, plants. Show only the architectural shell.\n"
            "2. **MISSING DOORS:** If the plan has a door but the photo doesn't, generate a **Flush Door** in the same color as the wall (minimalist, blending in).\n\n"

            "<CAMERA>\n"
            "1. **24mm LENS:** Wide enough to show both side walls and the ceiling/floor, but with less distortion than a fisheye.\n"
            "2. **Straight Verticals:** Keep vertical lines parallel.\n"
            
            "OUTPUT RULE: 16:9 Image. Geometry must match the Plan's specific corners and jogs. Materials extracted from photos. Strictly Empty."
        )
        
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        content_list = [prompt, plan_img] + ref_images
        
        response = call_gemini_with_failover(MODEL_NAME, content_list, {'timeout': 90}, safety_settings, system_instruction)
        
        if response and hasattr(response, 'candidates') and response.candidates:
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    out_filename = f"fp_result_{timestamp}_{unique_id}_{index}.png"
                    out_path = os.path.join("outputs", out_filename)
                    with open(out_path, 'wb') as f: f.write(part.inline_data.data)
                    
                    final_path = standardize_image(out_path)
                    return f"/outputs/{os.path.basename(final_path)}"
        return None
    except Exception as e:
        print(f"!! Single Room Gen Error {index}: {e}")
        return None

@app.post("/generate-room-from-plan")
def generate_room_from_plan(
    floor_plan: UploadFile = File(...),
    ref_photos: List[UploadFile] = File(...) 
):
    try:
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        print(f"\n=== [Floor Plan Gen] Starting 5 variations for {unique_id} ===", flush=True)

        plan_path = os.path.join("outputs", f"fp_plan_{timestamp}_{unique_id}.png")
        with open(plan_path, "wb") as buffer: shutil.copyfileobj(floor_plan.file, buffer)
        plan_img = Image.open(plan_path)

        ref_images = []
        for idx, ref_file in enumerate(ref_photos):
            ref_path = os.path.join("outputs", f"fp_ref_{timestamp}_{unique_id}_{idx}.png")
            with open(ref_path, "wb") as buffer: shutil.copyfileobj(ref_file.file, buffer)
            ref_images.append(Image.open(ref_path))
        
        print(f">> Loaded {len(ref_images)} reference photos.", flush=True)

        generated_results = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(generate_single_room_from_plan, plan_img, ref_images, unique_id, i+1) for i in range(5)]
            for future in futures:
                res = future.result()
                if res: generated_results.append(res)
        
        if generated_results:
            return JSONResponse(content={"urls": generated_results, "message": "Success"})
        else:
            return JSONResponse(content={"error": "Failed to generate images"}, status_code=500)
            
    except Exception as e:
        print(f"🔥🔥🔥 [Floor Plan Gen Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)


# =========================
# Video MVP (Kling Image-to-Video via Freepik API)
# =========================
class VideoClip(BaseModel):
    url: str
    preset: str = "detail_pan_lr"

class VideoCreateRequest(BaseModel):
    clips: List[VideoClip]
    duration: str = "5"          # Provider side (Kling fixed: always 5s clips)
    cfg_scale: float = 0.85

    # --- Auto "reference-style" mode (optional, backward compatible) ---
    # If mode is omitted, the server auto-detects by checking for preset == "ref_auto".
    mode: Optional[str] = None              # "auto_ref" | "manual"
    target_total_sec: Optional[float] = None  # default 20.0
    include_intro_outro: Optional[bool] = None # default True


# Use Freepik API key for Kling as well (same header: x-freepik-api-key)
FREEPIK_API_KEY = os.getenv("FREEPIK_API_KEY") or os.getenv("MAGNIFIC_API_KEY")  # fallback for existing env
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-5-pro")  # e.g. kling-v2-1-pro, kling-v2-5-pro
KLING_ENDPOINT = os.getenv("KLING_ENDPOINT", f"https://api.freepik.com/v1/ai/image-to-video/{KLING_MODEL}")

# Concurrency controls (avoid 429 bursts)
VIDEO_MAX_CONCURRENCY = int(os.getenv("VIDEO_MAX_CONCURRENCY", "2"))
_video_sem = threading.Semaphore(VIDEO_MAX_CONCURRENCY)

VIDEO_TARGET_FPS = int(os.getenv("VIDEO_TARGET_FPS", "24"))

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
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out_path.write_bytes(r.content)

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
        "-crf", str(VIDEO_CRF),
        "-preset", "veryfast",
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
    # [FIX] 16:9 가로 -> 4:5 세로 강제 중앙 크롭 (Shorts/Reels 스타일)
    # 복잡한 패딩/블러 로직을 제거하고, 화면을 꽉 채운 뒤 중앙을 자르는 방식 적용
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase," # 1. 빈공간 없이 꽉 채우도록 확대 (비율 유지)
        f"crop={target_w}:{target_h}," # 2. 목표 해상도만큼 중앙을 잘라냄
        f"setsar=1," # 3. 픽셀 비율 1:1 강제 (병합 오류 방지)
        f"fps={fps}" # 4. 프레임레이트 통일
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-filter_complex", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(VIDEO_CRF),
        "-preset", "veryfast",
        str(out_path),
    ]
    _run_ffmpeg(cmd)
import io
import math

# -------------------------
# Auto Reference-Style Video (Vision + Motion Prompts)
# -------------------------

# Reference video pacing (measured from /mnt/data/AI영상 리소스1.mp4)
# Content scenes (excluding black intro/outro), in seconds:
# 1) 3.49, 2) 0.76, 3) 0.63, 4) 1.53, 5) 0.86, 6) 1.26, 7) 0.63, 8) 0.83, 9) 1.00
REF_STYLE_SCENE_DURS_SEC = [3.49, 0.76, 0.63, 1.53, 0.86, 1.26, 0.63, 0.83, 1.00]
REF_STYLE_SCENES = [
    {"scene_type": "establish_push",     "prefer": {"shot_type": "wide"}},
    {"scene_type": "kitchen_corner_slide","prefer": {"tags_any": ["kitchen", "counter", "cabinet"]}},
    {"scene_type": "sink_texture_pan",   "prefer": {"tags_any": ["sink", "counter", "tile"]}},
    {"scene_type": "pendant_reveal_tilt", "prefer": {"flags_any": ["has_pendant_light", "has_lamp", "has_table"]}},
    {"scene_type": "window_sunlight_reveal","prefer": {"flags_any": ["has_window", "has_blinds_or_curtain"]}},
    {"scene_type": "corridor_slide_forward","prefer": {"shot_type": "wide"}},
    {"scene_type": "desk_close_push",    "prefer": {"flags_any": ["has_desk"]}},
    {"scene_type": "chair_detail_pan",   "prefer": {"tags_any": ["chair", "stool", "armchair"]}},
    {"scene_type": "entry_mirror_pullback","prefer": {"flags_any": ["has_mirror", "has_entryway"]}},
]

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

def _vision_analyze_for_video_motion(img_bytes: bytes) -> Dict[str, Any]:
    """
    Vision analysis (Gemini-3-flash-preview) to choose a focal subject and safe motion.
    Returns a dict with keys used by the prompt builder.
    """
    try:
        img = Image.open(io.BytesIO(img_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")

        prompt = (
            "You are a meticulous vision analyst for interior photography.\n"
            "Analyze the provided image and output STRICT JSON only (no markdown).\n\n"
            "Return keys:\n"
            "{\n"
            '  "shot_type": "wide" | "detail",\n'
            '  "focal_object": "short noun phrase (e.g., pendant light, desk, blinds, sofa, mirror)",\n'
            '  "key_objects": ["list of notable objects, short nouns"],\n'
            '  "tags": ["kitchen","sink","counter","window","desk","chair","mirror","sofa","lamp","table","blinds","curtain","art","plant","bed","bathroom","entryway"],\n'
            '  "has_window": true/false,\n'
            '  "has_blinds_or_curtain": true/false,\n'
            '  "has_pendant_light": true/false,\n'
            '  "has_lamp": true/false,\n'
            '  "has_desk": true/false,\n'
            '  "has_mirror": true/false,\n'
            '  "has_entryway": true/false,\n'
            '  "composition": "where is the focal object (center/left/right/foreground/background)",\n'
            '  "lighting": "daylight/soft daylight/low light/unknown"\n'
            "}\n\n"
            "Rules:\n"
            "- Do NOT invent objects that are not visible.\n"
            "- If unsure, be conservative and set booleans to false.\n"
        )
        resp = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [prompt, img], {"timeout": 45}, {})
        data = _safe_extract_json(resp.text if resp else "")
        if not data:
            raise ValueError("Empty JSON from vision")

        data.setdefault("tags", [])
        data.setdefault("key_objects", [])
        data.setdefault("shot_type", "detail")
        data.setdefault("focal_object", "main focal point")
        for k in ["has_window","has_blinds_or_curtain","has_pendant_light","has_lamp","has_desk","has_mirror","has_entryway"]:
            if k not in data:
                data[k] = False
        return data
    except Exception as e:
        print(f"⚠️ [Vision Analyze Failed] {e}", flush=True)
        return {
            "shot_type": "detail",
            "focal_object": "main focal point",
            "key_objects": [],
            "tags": [],
            "has_window": False,
            "has_blinds_or_curtain": False,
            "has_pendant_light": False,
            "has_lamp": False,
            "has_desk": False,
            "has_mirror": False,
            "has_entryway": False,
            "composition": "unknown",
            "lighting": "unknown",
        }

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
    Turns a still image into a short video segment (used for intro/outro).
    """
    fade_d = 0.25
    fade_out_st = max(0.0, dur_sec - fade_d)
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},setsar=1,fps={fps},"
        f"fade=t=in:st=0:d={fade_d},fade=t=out:st={fade_out_st}:d={fade_d}"
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
        "-crf", str(VIDEO_CRF),
        "-preset", "veryfast",
        str(out_path),
    ]
    _run_ffmpeg(cmd)

def _pick_best_url(all_urls: List[str], analyses: Dict[str, Dict[str, Any]], prefer: Dict[str, Any], used: set) -> str:
    """
    Simple heuristic selector for a scene. Falls back to first URL.
    """
    def score(u: str) -> int:
        a = analyses.get(u, {})
        sc = 0
        if prefer.get("shot_type") and a.get("shot_type") == prefer["shot_type"]:
            sc += 3
        tags_any = prefer.get("tags_any") or []
        if tags_any:
            at = set([t.lower() for t in (a.get("tags") or [])])
            if any(t in at for t in [x.lower() for x in tags_any]):
                sc += 3
        flags_any = prefer.get("flags_any") or []
        if flags_any and any(bool(a.get(f)) for f in flags_any):
            sc += 3
        if u not in used:
            sc += 1
        return sc

    urls = list(all_urls)
    urls.sort(key=score, reverse=True)
    return urls[0] if urls else ""

def _build_auto_ref_plan(
    input_clips: List[VideoClip],
    analyses: Dict[str, Dict[str, Any]],
    target_total_sec: float,
    include_intro_outro: bool,
) -> List[Dict[str, Any]]:
    """
    Build a shot plan that matches the reference video's pacing,
    scales it to ~target_total_sec, and reuses images if necessary.
    """
    speed = VIDEO_SPEED_FACTOR if VIDEO_SPEED_FACTOR > 0 else 2.0
    max_out_per_clip = VIDEO_PROVIDER_CLIP_SEC / speed  # e.g., 5/2 = 2.5s

    intro_out = 1.0
    outro_out = 1.0
    intro_src = intro_out * speed
    outro_src = outro_out * speed

    plan: List[Dict[str, Any]] = []
    used_urls = set()

    if include_intro_outro:
        intro_img = _find_static_image("intro")
        if intro_img:
            plan.append({"kind": "still", "scene_type": "intro", "image_path": str(intro_img), "src_dur": intro_src})

    urls = [c.url for c in input_clips]
    content_target = max(
        2.0,
        target_total_sec
        - (intro_out if (include_intro_outro and _find_static_image("intro")) else 0.0)
        - (outro_out if (include_intro_outro and _find_static_image("outro")) else 0.0)
    )
    ref_sum = sum(REF_STYLE_SCENE_DURS_SEC) or 1.0
    scale = content_target / ref_sum
    scaled_out_durs = [d * scale for d in REF_STYLE_SCENE_DURS_SEC]

    leftover = 0.0
    for idx, scene in enumerate(REF_STYLE_SCENES):
        scene_type = scene["scene_type"]
        desired_out = scaled_out_durs[idx]
        req = min(2, math.ceil(desired_out / max_out_per_clip))
        per_out = min(desired_out / req, max_out_per_clip)
        produced_out = per_out * req
        if produced_out + 1e-6 < desired_out:
            leftover += (desired_out - produced_out)

        url = _pick_best_url(urls, analyses, scene.get("prefer", {}), used_urls)
        if url:
            used_urls.add(url)

        for k in range(req):
            plan.append({
                "kind": "kling",
                "scene_type": scene_type if req == 1 else f"{scene_type}_part{k+1}",
                "url": url,
                "src_dur": min(VIDEO_PROVIDER_CLIP_SEC, per_out * speed),
            })

    if leftover > 0.25:
        filler_url = _pick_best_url(urls, analyses, {"shot_type": "wide"}, used_urls) or (urls[0] if urls else "")
        plan.append({
            "kind": "kling",
            "scene_type": "closing_hold",
            "url": filler_url,
            "src_dur": min(VIDEO_PROVIDER_CLIP_SEC, leftover * speed),
        })

    if include_intro_outro:
        outro_img = _find_static_image("outro")
        if outro_img:
            plan.append({"kind": "still", "scene_type": "outro", "image_path": str(outro_img), "src_dur": outro_src})

    return plan

def _kling_prompts_for_ref_auto(scene_type: str, vision: Dict[str, Any]) -> Dict[str, str]:
    """
    Prompt builder that tries to replicate the reference video's 'premium interior walkthrough' feel,
    while staying robust for arbitrary interior images.
    """
    base_keep = (
        "Keep ALL objects, materials, and room layout EXACTLY the same as the input image. "
        "Do not add or remove anything. No warping, no melting, no object morphing. Photorealistic."
    )
    daylight = (
        "Neutral daylight (5200–5600K). Natural contrast, clean whites, no warm/yellow cast. "
        "Crisp but soft-edged shadows."
    )
    subject = (vision.get("focal_object") or "main focal point").strip()
    motion_common = "Motion should be smooth and moderate (will be sped up 2x later). Gimbal-stabilized, premium."

    window_fx = ""
    if vision.get("has_window") and vision.get("has_blinds_or_curtain"):
        window_fx = "If blinds/curtains are visible, make a VERY subtle movement that reveals slightly stronger sunlight (no deformation). "

    pendant_fx = ""
    if vision.get("has_pendant_light") or vision.get("has_lamp"):
        pendant_fx = "If a light fixture is visible, allow extremely subtle sway (1–2 degrees) and realistic specular highlights. "

    if scene_type.startswith("establish_push"):
        cam = "Slow dolly-in with a tiny pan to the right, slight parallax, maintain straight verticals."
    elif scene_type.startswith("kitchen_corner_slide"):
        cam = "Smooth lateral slide (left) with a slight tilt, like a short slider move."
    elif scene_type.startswith("sink_texture_pan"):
        cam = "Close-up texture shot. Camera pans left-to-right across surfaces, shallow depth of field."
    elif scene_type.startswith("pendant_reveal_tilt"):
        cam = "Start closer to the ceiling/fixture area then tilt down slightly to reveal the tabletop/space, smooth and premium."
    elif scene_type.startswith("window_sunlight_reveal"):
        cam = "Wide shot. Gentle pan and micro push-in toward the window area, emphasizing daylight."
    elif scene_type.startswith("corridor_slide_forward"):
        cam = "Wide shot. Smooth forward slide through the space with a slight pan, like a walkthrough reveal."
    elif scene_type.startswith("desk_close_push"):
        cam = "Close-up. Slow push-in toward the desk/work area with a subtle rack-focus feel (no focus hunting)."
    elif scene_type.startswith("chair_detail_pan"):
        cam = "Close-up. Pan right-to-left across the chair/seating detail, smooth premium movement."
    elif scene_type.startswith("entry_mirror_pullback"):
        cam = "Wide-ish shot. Slow pull-back (dolly-out) to reveal the entry/mirror area, stable geometry."
    elif scene_type.startswith("closing_hold"):
        cam = "Almost static hold with extremely subtle drift, like a calm ending beat."
    else:
        cam = "Smooth cinematic camera move, premium and stable."

    prompt = (
        f"{base_keep} {daylight} "
        f"Hero focus: {subject}. "
        f"{cam} {motion_common} "
        f"{window_fx}{pendant_fx}"
        "No people."
    )

    neg = (
        "warm yellow lighting, tungsten, sepia, orange cast, "
        "object teleporting, new decor, extra furniture, extra lamps, extra shelves, "
        "people, hands, faces, text, watermark, logo, frame borders, "
        "geometry distortion, melting, wobble, flicker, rolling shutter, "
        "changing materials, changing room layout, changing perspective"
    )
    return {"prompt": prompt, "negative_prompt": neg}

def _kling_prompts_for_preset(preset: str) -> Dict[str, str]:
    # Keep prompts short + strict to reduce hallucinated object changes.
    base_keep = (
        "Keep ALL objects and layout exactly the same as the input image. "
        "No new objects, no removals, no warping. Photorealistic."
    )
    daylight = (
        "Strong neutral daylight from the window (5200–5600K), no warm/yellow cast. "
        "Sunlight is slightly stronger than usual with crisp but soft-edged shadows."
    )

    if preset == "main_sunlight":
        p = f"{base_keep} {daylight} Gentle cinematic camera move, slightly more dynamic than static."
    elif preset == "orbit_rotate":
        p = f"{base_keep} {daylight} Slow orbit rotation around the main furniture (10–15° arc), keep subject centered."
    elif preset == "orbit_rotate_ccw":
        p = f"{base_keep} {daylight} Slow orbit rotation (counter-clockwise 10–15° arc), keep subject centered."
    elif preset == "detail_pan_lr":
        p = f"{base_keep} {daylight} Close-up shot. Camera pans left-to-right with a bit faster motion, smooth and premium."
    elif preset == "detail_pan_rl":
        p = f"{base_keep} {daylight} Close-up shot. Camera pans right-to-left with a bit faster motion, smooth and premium."
    elif preset == "detail_dolly_in":
        p = f"{base_keep} {daylight} Close-up shot. Camera dolly-in (push-in) toward the subject, smooth premium movement."
    elif preset == "detail_dolly_out":
        p = f"{base_keep} {daylight} Close-up shot. Camera dolly-out (pull-back), smooth premium movement."
    elif preset == "tilt_down":
        p = f"{base_keep} {daylight} Camera tilts down slightly to reveal the main subject, smooth premium movement."
    elif preset == "static":
        p = f"{base_keep} {daylight} Almost static shot with extremely subtle camera drift."
    else:
        p = f"{base_keep} {daylight} Smooth premium camera move."

    neg = (
        "warm yellow lighting, tungsten, sepia, orange cast, "
        "object teleporting, new decor, extra vases, cats, shelves, lamps, "
        "geometry distortion, flicker, text, watermark, logo, frame borders"
    )
    return {"prompt": p, "negative_prompt": neg}

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
    
    # ✅ 디버깅: 실제 응답 구조 출력
    print(f"🔍 [DEBUG] Kling API Response: {json.dumps(data, indent=2)}", flush=True)
    
    # 여러 가능한 필드 시도
    task_id = (
        data.get("task_id") or 
        data.get("id") or 
        data.get("data", {}).get("task_id") or 
        data.get("data", {}).get("id") or
        data.get("result", {}).get("task_id") or
        data.get("taskId")
    )
    
    if not task_id:
        print(f"❌ [ERROR] Could not find task_id. Full response keys: {list(data.keys())}", flush=True)
        raise RuntimeError(f"No task_id returned from Kling create. Response: {json.dumps(data)[:300]}")
    
    print(f"✅ [SUCCESS] Task created: {task_id}", flush=True)
    return task_id

import math # 함수 상단이나 파일 최상단에 import math 필요

def _freepik_kling_poll(task_id: str, job_id: str, clip_index: int, total_clips: int, timeout_sec: int = 600) -> str:
    headers = {"x-freepik-api-key": FREEPIK_API_KEY}
    start = time.time()
    poll_count = 0
    
    # [UX] 각 클립당 할당할 최대 진행률 (전체의 90%를 클립 생성에 분배)
    # 예: 클립이 1개면 90%까지, 2개면 개당 45%까지 할당
    clip_share_percent = 90 / max(1, total_clips)
    clip_start_percent = clip_index * clip_share_percent

    while True:
        if time.time() - start > timeout_sec:
            raise RuntimeError("Kling task timeout.")
        
        poll_count += 1
        
        # 1. API 호출 (네트워크 에러 방어)
        try:
            with _video_sem:
                r = requests.get(f"{KLING_ENDPOINT}/{task_id}", headers=headers, timeout=60)
            
            if not r.ok:
                # 500 에러 등은 잠시 대기 후 재시도
                if r.status_code >= 500:
                    print(f"⚠️ [Server Warning] {r.status_code}. Retrying...", flush=True)
                    time.sleep(3)
                    continue
                raise RuntimeError(f"Kling status failed ({r.status_code}): {r.text[:300]}")
                
            st = r.json()
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️ [Network Warning] Polling failed temporarily: {e}. Retrying...", flush=True)
            time.sleep(3)
            continue

        # 2. [FIX] 데이터 구조 방어 로직 (AttributeError 'str' object 방지)
        data = st.get("data", {})
        status = "UNKNOWN"

        if isinstance(data, dict):
            status = data.get("status", "").upper()
        elif isinstance(st, dict):
             # data가 없거나 문자열이면 top-level에서 status 확인
            status = st.get("status", "").upper()
        
        # 3. [FIX] 진행률 로직 개선 (15% 멈춤 해결)
        # 로그 함수를 사용하여 시간이 지날수록 천천히 오르지만 100%는 넘지 않게 설정
        # poll_count가 늘어날수록 clip_share_percent의 95% 수준까지 점진적으로 접근
        simulated_progress = clip_share_percent * 0.95 * (1 - math.exp(-0.05 * poll_count))
        
        current_total_progress = int(clip_start_percent + simulated_progress)
        
        # 로그 출력 (사용자 안심용)
        if poll_count <= 3 or poll_count % 5 == 0:
            print(f"🔍 [Poll #{poll_count}] Clip {clip_index+1}/{total_clips} Status: {status} (Progress: {current_total_progress}%)", flush=True)

        with video_jobs_lock:
            if job_id in video_jobs:
                video_jobs[job_id]["progress"] = current_total_progress
                # 메시지에 실제 서버 상태 포함
                video_jobs[job_id]["message"] = f"Generating clip {clip_index+1}/{total_clips}: {status}..."
        
        # 4. 완료 처리
        if status in ("COMPLETED", "SUCCEEDED", "SUCCESS", "DONE"):
            print(f"✅ [COMPLETED] Clip {clip_index+1}/{total_clips}. Fetching URL...", flush=True)
            
            # generated 필드 안전 추출
            generated = []
            if isinstance(data, dict):
                generated = data.get("generated", [])
            elif isinstance(st, dict):
                generated = st.get("generated", [])

            # 완료되었는데 URL이 바로 안 뜨는 경우 대기
            retry_count = 0
            while not generated and retry_count < 5:
                print(f"⏳ [WAIT] Generated array empty, retrying... ({retry_count+1}/5)", flush=True)
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

            # URL 찾기
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
                print(f"✅ [SUCCESS] Found URL: {url[:60]}...", flush=True)
                return url
            
            print(f"❌ [ERROR] Completed but no URL. Response dump:", flush=True)
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
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return base64.b64encode(r.content).decode("utf-8")

def _run_video_job(
    job_id: str,
    clips: List[VideoClip],
    duration: str,
    cfg_scale: float,
    mode: Optional[str],
    target_total_sec: Optional[float],
    include_intro_outro: Optional[bool],
):
    """
    Video job runner.

    - manual mode: uses the client-provided clip list and preset prompts.
    - auto_ref mode: ignores per-clip presets and generates a reference-style shot plan:
        * vision analysis (gemini-3-flash-preview) for each input image
        * scene pacing modeled after the reference video
        * intro/outro from static/intro.* and static/outro.* (if present)
        * every Kling clip is generated as 5s, then sped up x2 (and optionally trimmed) before stitching
    """
    try:
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "RUNNING"
            video_jobs[job_id]["message"] = "Preparing clips..."
            video_jobs[job_id]["progress"] = 1

        out_dir = Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)

        # Kling duration is fixed (always 5s on provider side)
        duration = "5"

        # Mode resolution (backward compatible)
        effective_mode = (mode or "").strip().lower()
        if not effective_mode:
            effective_mode = "auto_ref" if any((c.preset or "") == "ref_auto" for c in clips) else "manual"

        tgt_total = float(target_total_sec) if target_total_sec else 20.0
        use_intro_outro = True if include_intro_outro is None else bool(include_intro_outro)

        # Cache image bytes/b64 for reuse + vision results
        bytes_cache: Dict[str, bytes] = {}
        b64_cache: Dict[str, str] = {}
        vision_cache: Dict[str, Dict[str, Any]] = {}

        def get_bytes(url: str) -> bytes:
            if url not in bytes_cache:
                bytes_cache[url] = _clip_url_to_image_bytes(url)
            return bytes_cache[url]

        def get_b64(url: str) -> str:
            if url not in b64_cache:
                b64_cache[url] = base64.b64encode(get_bytes(url)).decode("utf-8")
            return b64_cache[url]

        # Build plan
        if effective_mode == "auto_ref":
            # Vision analysis for each unique input image
            unique_urls = list(dict.fromkeys([c.url for c in clips]))
            for u in unique_urls:
                vision_cache[u] = _vision_analyze_for_video_motion(get_bytes(u))

            plan = _build_auto_ref_plan(
                input_clips=clips,
                analyses=vision_cache,
                target_total_sec=tgt_total,
                include_intro_outro=use_intro_outro,
            )
        else:
            # Manual mode: 1 input image -> 1 clip
            plan = [{"kind": "kling", "scene_type": c.preset, "url": c.url, "src_dur": VIDEO_TRIM_KEEP_SEC} for c in clips]

        # Count Kling tasks for progress UI
        total_kling = sum(1 for p in plan if p.get("kind") == "kling")
        kling_idx = 0

        generated_paths = []
        for plan_idx, item in enumerate(plan):
            kind = item.get("kind")
            scene_type = item.get("scene_type", "scene")
            src_dur = float(item.get("src_dur") or VIDEO_TRIM_KEEP_SEC)

            if kind == "still":
                with video_jobs_lock:
                    video_jobs[job_id]["message"] = f"Preparing {scene_type}..."
                    video_jobs[job_id]["progress"] = min(65, 5 + int((plan_idx / max(1, len(plan))) * 60))

                raw_path = out_dir / f"video_raw_{job_id}_{plan_idx}.mp4"
                img_path = Path(item["image_path"])
                _ffmpeg_image_to_video(img_path, raw_path, src_dur, 1080, 1350, VIDEO_TARGET_FPS)

            else:
                # Kling clip
                with video_jobs_lock:
                    video_jobs[job_id]["message"] = f"Generating clip {kling_idx+1}/{total_kling} ({scene_type})..."
                    video_jobs[job_id]["progress"] = int((kling_idx / max(1, total_kling)) * 60) + 5

                url = item["url"]

                if effective_mode == "auto_ref":
                    prompts = _kling_prompts_for_ref_auto(scene_type, vision_cache.get(url, {}))
                else:
                    prompts = _kling_prompts_for_preset(item.get("scene_type") or "smooth_zoom")

                img_b64 = get_b64(url)

                task_id = _freepik_kling_create_task(
                    img_b64,
                    prompts["prompt"],
                    prompts["negative_prompt"],
                    duration,
                    cfg_scale,
                )

                video_url = _freepik_kling_poll(task_id, job_id, kling_idx, total_kling)
                kling_idx += 1

                raw_path = out_dir / f"video_raw_{job_id}_{plan_idx}.mp4"
                _download_to_path(video_url, raw_path)

            # Trim (optional) + speed-up x2 (required) for each segment
            trimmed_path = out_dir / f"video_trim_{job_id}_{plan_idx}.mp4"
            _ffmpeg_trim_speed(raw_path, trimmed_path, 0.0, src_dur, VIDEO_SPEED_FACTOR, VIDEO_TARGET_FPS)
            generated_paths.append(trimmed_path)

        if not generated_paths:
            raise RuntimeError("No clips generated.")

        # 2) Normalize (force 4:5 1080x1350)
        ref_w, ref_h = 1080, 1350
        normalized_paths = []
        for i, p in enumerate(generated_paths):
            with video_jobs_lock:
                video_jobs[job_id]["message"] = f"Normalizing clip {i+1}/{len(generated_paths)}..."
                video_jobs[job_id]["progress"] = 70 + int((i / max(1, len(generated_paths))) * 15)

            norm = out_dir / f"video_norm_{job_id}_{i}.mp4"
            _ffmpeg_normalize_to(p, norm, ref_w, ref_h, VIDEO_TARGET_FPS)
            normalized_paths.append(norm)

        # 3) Concat
        with video_jobs_lock:
            video_jobs[job_id]["message"] = "Stitching final video..."
            video_jobs[job_id]["progress"] = 90

        list_file = out_dir / f"video_concat_{job_id}.txt"
        list_lines = [f"file '{p.resolve().as_posix()}'" for p in normalized_paths]
        list_file.write_text("\n".join(list_lines), encoding="utf-8")

        final_path = out_dir / f"video_final_{job_id}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(final_path),
        ]
        try:
            _run_ffmpeg(cmd)
        except Exception:
            cmd2 = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", str(VIDEO_CRF),
                "-preset", "veryfast",
                "-an",
                str(final_path),
            ]
            _run_ffmpeg(cmd2)

        result_url = f"/outputs/{final_path.name}"

        with video_jobs_lock:
            video_jobs[job_id]["status"] = "COMPLETED"
            video_jobs[job_id]["message"] = "Completed"
            video_jobs[job_id]["progress"] = 100
            video_jobs[job_id]["result_url"] = result_url

    except Exception as e:
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "FAILED"
            video_jobs[job_id]["error"] = str(e)
            video_jobs[job_id]["message"] = "FAILED"
            video_jobs[job_id]["progress"] = 0


@app.post("/video-mvp/create")
async def video_mvp_create(req: VideoCreateRequest):
    job_id = uuid.uuid4().hex
    with video_jobs_lock:
        video_jobs[job_id] = {
            "status": "QUEUED",
            "message": "Queued",
            "progress": 0,
            "result_url": None,
            "error": None,
        }
    video_executor.submit(_run_video_job, job_id, req.clips, req.duration, req.cfg_scale, req.mode, req.target_total_sec, req.include_intro_outro)
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
            
            # 1. 파일 정리 (기존 로직 유지)
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
            
            # 2. [FIX] 메모리 정리: 완료되었거나 오래된 Job ID 삭제 (메모리 누수 방지)
            # Job 생성 후 24시간(86400초) 지난 기록은 삭제
            JOB_RETENTION = 86400 
            with video_jobs_lock:
                # 딕셔너리를 순회하며 삭제해야 하므로 키 리스트 복사 사용
                for jid in list(video_jobs.keys()):
                    # progress가 100이거나 failed인 상태에서 오래된 것, 혹은 그냥 너무 오래된 것 삭제
                    # 여기서는 단순하게 생성 시간을 별도 추적 안하므로, 일단 100% 완료된 건 바로 지우지 않고(다운로드 위해),
                    # 리스트 관리 정책이 필요함.
                    # 간단하게: video_jobs에 timestamp 필드를 추가하는 것이 정석이나,
                    # 현재 구조상 '너무 많아지면 강제 정리' 방식으로 구현.
                    if len(video_jobs) > 1000: # 혹시 1000개가 넘어가면
                        video_jobs.pop(jid, None) # 앞에서부터 하나 지움 (Python 3.7+ 딕셔너리는 삽입 순서 유지되므로 가장 오래된 것 삭제됨)
            
            if deleted_count > 0:
                print(f"✨ [System] Cleaned up {deleted_count} old files.", flush=True)
                
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