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

    valid_items = [] # [변경] 단순 숫자 리스트가 아니라 객체 리스트로 변경
    valid_exts = ('.png', '.jpg', '.jpeg', '.webp')

    try:
        for f in os.listdir(base_dir):
            f_lower = f.lower()
            if f_lower.startswith(prefix) and f_lower.endswith(valid_exts):
                try:
                    name_part = f_lower.replace(prefix, "")
                    num_part = os.path.splitext(name_part)[0]
                    if num_part.isdigit():
                        # [변경] 번호와 '실제 파일명'을 함께 저장
                        valid_items.append({"index": int(num_part), "file": f})
                except: continue
        
        # 번호 순서대로 정렬
        valid_items.sort(key=lambda x: x["index"])
        return valid_items
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
            
            # [수정] 폴더 대소문자 무시하고 찾기 로직
            target_path = os.path.join("assets", safe_room, safe_style)
            assets_dir = None

            # 1. 정확한 경로가 있으면 사용
            if os.path.exists(target_path):
                assets_dir = target_path
            else:
                # 2. 없으면 대소문자 무시하고 탐색 (assets 폴더 안을 뒤짐)
                # 예: 코드는 'livingroom'을 찾지만 폴더는 'LivingRoom'이어도 찾게 함
                root_assets = "assets"
                if os.path.exists(root_assets):
                    # Room 찾기
                    found_room = next((d for d in os.listdir(root_assets) if d.lower() == safe_room), None)
                    if found_room:
                        room_path = os.path.join(root_assets, found_room)
                        # Style 찾기
                        found_style = next((d for d in os.listdir(room_path) if d.lower() == safe_style), None)
                        if found_style:
                            assets_dir = os.path.join(room_path, found_style)

            # 폴더를 찾았으면 파일 검색 시작
            if assets_dir and os.path.exists(assets_dir):
                files = sorted(os.listdir(assets_dir))
                found = False
                import re 
                
                # 파일명 검색 (대소문자 무시 플래그 re.IGNORECASE 추가)
                pattern = rf"(?:^|[^0-9]){re.escape(variant)}(?:[^0-9]|$)"
                
                # 지원할 확장자
                valid_exts = ('.png', '.jpg', '.jpeg', '.webp')

                for f in files:
                    # 확장자 체크 & 번호 매칭 (대소문자 무시)
                    if f.lower().endswith(valid_exts) and re.search(pattern, f, re.IGNORECASE):
                        ref_path = os.path.join(assets_dir, f)
                        # URL 경로 생성 시 역슬래시(\)를 슬래시(/)로 바꿔야 웹에서 안깨짐
                        mb_url = f"/assets/{os.path.basename(os.path.dirname(assets_dir))}/{os.path.basename(assets_dir)}/{f}"
                        found = True
                        break
                
                # 못 찾았는데 파일이 있다면 첫번째 파일 사용 (확장자 맞는 것 중)
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

# [수정] main.py 내부의 generate_details_endpoint 함수 교체

@app.post("/generate-details")
def generate_details_endpoint(req: DetailRequest):
    try:
        # 1. 대상 이미지 경로 확보
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "Original image not found"}, status_code=404)

        unique_id = uuid.uuid4().hex[:6]
        print(f"\n=== [Detail View] 요청 시작 ({unique_id}) - Smart Analysis Mode ===", flush=True)

        analyzed_items = []
        
        # 2. 가구 데이터 확인 (캐시 or 신규 분석)
        if req.furniture_data and len(req.furniture_data) > 0:
            print(">> [Smart Cache] Using pre-analyzed furniture data!", flush=True)
            analyzed_items = req.furniture_data
        else:
            print(">> [Smart Cache] No cached data found. Starting Analysis...", flush=True)
            
            # [NEW] 분석할 대상 이미지 결정 로직 (무드보드 우선 -> 없으면 메인 이미지 사용)
            target_analysis_path = None
            
            if req.moodboard_url:
                # A. 무드보드 URL이 있는 경우 (경로 파싱)
                if req.moodboard_url.startswith("/assets/"):
                    rel_path = req.moodboard_url.lstrip("/")
                    target_analysis_path = os.path.join(*rel_path.split("/"))
                else:
                    mb_filename = os.path.basename(req.moodboard_url)
                    target_analysis_path = os.path.join("outputs", mb_filename)
            else:
                # B. [핵심 수정] 무드보드가 없으면? -> 메인 이미지를 분석 대상으로 설정!
                print(">> [Info] No Moodboard provided. Analyzing the Main Image itself.", flush=True)
                target_analysis_path = local_path

            # 3. 실제 분석 실행
            if target_analysis_path and os.path.exists(target_analysis_path):
                try:
                    detected_items = detect_furniture_boxes(target_analysis_path)
                    print(f">> [Deep Analysis] Found {len(detected_items)} items in {target_analysis_path}...", flush=True)
                    
                    with ThreadPoolExecutor(max_workers=10) as executor: # Worker 수 약간 증량
                        futures = [executor.submit(analyze_cropped_item, target_analysis_path, item) for item in detected_items]
                        analyzed_items = [f.result() for f in futures]
                        
                    print(f">> [Analysis Done] Items: {[item['label'] for item in analyzed_items]}", flush=True)
                except Exception as e:
                    print(f"!! Analysis Failed: {e}. Using defaults.", flush=True)
                    analyzed_items = []
            else:
                 print(f"!! Target path not found: {target_analysis_path}", flush=True)

            # 4. 분석 실패 시 최후의 보루 (기본값)
            if not analyzed_items:
                 print("!! Fallback to default list.", flush=True)
                 analyzed_items = [{"label": "Sofa"}, {"label": "Chair"}, {"label": "Table"}]
        
        # 5. 동적 스타일 구성 및 생성 요청
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
    motion: str = "static"  # 기본값
    effect: str = "none"    # 기본값

class VideoCreateRequest(BaseModel):
    clips: List[VideoClip]
    duration: str = "5"
    cfg_scale: float = 0.85
    mode: Optional[str] = None
    target_total_sec: Optional[float] = None
    include_intro_outro: Optional[bool] = None
    # [필수 확인]
    intro_url: Optional[str] = None
    outro_url: Optional[str] = None


# Use Freepik API key for Kling as well (same header: x-freepik-api-key)
FREEPIK_API_KEY = os.getenv("FREEPIK_API_KEY") or os.getenv("MAGNIFIC_API_KEY")  # fallback for existing env
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-5-pro")  # e.g. kling-v2-1-pro, kling-v2-5-pro
KLING_ENDPOINT = os.getenv("KLING_ENDPOINT", f"https://api.freepik.com/v1/ai/image-to-video/{KLING_MODEL}")

# Concurrency controls (avoid 429 bursts)
VIDEO_MAX_CONCURRENCY = int(os.getenv("VIDEO_MAX_CONCURRENCY", "5"))
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
    """
    URL이 http로 시작하면 다운로드하고,
    / 로 시작하면 로컬 파일을 복사합니다.
    """
    # [수정] 로컬 파일 경로인 경우 (/outputs/... 등)
    if url.startswith("/"):
        # 맨 앞의 슬래시 제거 (절대경로 -> 상대경로 변환, 예: /outputs/a.png -> outputs/a.png)
        local_path = url.lstrip("/")
        
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found on server: {local_path}")
            
        # 단순히 파일 복사
        with open(local_path, "rb") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return

    # [기존] 원격 URL인 경우 (http://...)
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

# [NEW] 모션과 이펙트를 조합하여 프롬프트 생성
def _kling_prompts_dynamic(motion: str, effect: str) -> Dict[str, str]:
    # 1. 기본 품질 및 유지 프롬프트
    base_keep = (
        "High quality interior video, photorealistic, 8k. "
        "Keep ALL furniture and layout exactly the same as the input image. "
        "No warping, no distortion. "
    )
    
    # 2. 모션 프롬프트 매핑
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
    
    # 3. 이펙트 프롬프트 매핑
    effect_map = {
        "none": "Natural lighting, static environment.",
        "sunlight": "Sunlight beams moving across the room, time-lapse shadow movement on the floor and furniture.",
        "lights_on": "Lighting transition: starts with lights off or dim, then lights turn on brightly. Cinematic illumination reveal.",
        "blinds": "Curtains or blinds moving gently in the wind near the window.",
        "plants": "Indoor plants and foliage swaying gently in a soft breeze.",
        "door_open": "A door, cabinet door, or glass door in the scene slowly opens.",
    }

    # 프롬프트 조합
    p_motion = motion_map.get(motion, motion_map["static"])
    p_effect = effect_map.get(effect, effect_map["none"])
    
    final_prompt = f"{base_keep} {p_motion} {p_effect}"

    # 네거티브 프롬프트
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
    """
    이미지 URL(혹은 로컬 경로)을 받아 Base64 문자열로 변환합니다.
    """
    # [수정] 로컬 파일 경로인 경우
    if url.startswith("/"):
        local_path = url.lstrip("/")
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found for b64 conversion: {local_path}")
            
        with open(local_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    # [기존] 원격 URL인 경우
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return base64.b64encode(r.content).decode("utf-8")

# -----------------------------------------------------------------------------
# [NEW] 단일 클립 처리 함수 (병렬 실행용)
# -----------------------------------------------------------------------------
def process_single_clip(idx, item, job_id, out_dir, total_steps, completed_counter_lock, completed_counter):
    """
    하나의 클립(정지 화상 또는 Kling 영상)을 생성하고 가공하는 단위 작업입니다.
    """
    try:
        kind = item.get("kind")
        user_preset = item.get("preset", "detail_pan_lr")
        src_dur = item["dur"]
        
        raw_path = out_dir / f"v_raw_{job_id}_{idx}.mp4"
        final_clip_path = out_dir / f"v_clip_{job_id}_{idx}.mp4"

        # 진행 상황 업데이트 (메시지만)
        with video_jobs_lock:
            if job_id in video_jobs:
                video_jobs[job_id]["message"] = f"Generating clip {idx+1} ({kind})..."

        # [Static] 정지 영상 (FFmpeg)
        if kind == "still" or user_preset == "static":
            temp_img = out_dir / f"tmp_{job_id}_{idx}.png"
            try:
                _download_to_path(item["url"], temp_img)
                # 정지 영상 생성
                _ffmpeg_image_to_video(temp_img, raw_path, src_dur, 1080, 1920, VIDEO_TARGET_FPS)
            except Exception as e:
                print(f"Static Gen Error (Clip {idx}): {e}")
                raise e
            finally:
                if temp_img.exists(): temp_img.unlink()

            # 길이만 맞춤 (속도 변화 없음)
            _ffmpeg_trim_speed(raw_path, final_clip_path, 0.0, src_dur, 1.0, VIDEO_TARGET_FPS)

        # [Motion] Kling AI (5초 생성 -> 2배속 -> 2.5초 결과물)
        elif kind == "kling":
            # [변경] 기존 user_preset 대신 motion, effect 사용
            user_motion = item.get("motion", "static")
            user_effect = item.get("effect", "none")
            
            # [변경] 새로운 함수 호출
            prompts = _kling_prompts_dynamic(user_motion, user_effect)
            
            img_b64 = _image_url_to_b64(item["url"])
            
            task_id = _freepik_kling_create_task(
                img_b64, prompts["prompt"], prompts["negative_prompt"], 
                "5", 0.5
            )
            # 폴링 (타임아웃 등은 내부 처리)
            video_url = _freepik_kling_poll(task_id, job_id, idx, total_steps)
            _download_to_path(video_url, raw_path)
            
            # [중요] 5초 영상을 2.5초로 만드니까 정확히 2배속(Speed x2)이 됨
            _ffmpeg_trim_speed(raw_path, final_clip_path, 0.0, src_dur, VIDEO_SPEED_FACTOR, VIDEO_TARGET_FPS)

        # 작업 완료 후 진행률 업데이트
        with completed_counter_lock:
            completed_counter[0] += 1
            current_count = completed_counter[0]
        
        # 전체 진행률(80%까지) 반영
        progress_percent = int((current_count / total_steps) * 80)
        with video_jobs_lock:
            if job_id in video_jobs:
                video_jobs[job_id]["progress"] = progress_percent
                video_jobs[job_id]["message"] = f"Finished clip {idx+1}/{total_steps}"

        return (idx, final_clip_path)

    except Exception as e:
        print(f"!! Clip {idx} Failed: {e}")
        traceback.print_exc()
        raise e

# -----------------------------------------------------------------------------
# [수정] 메인 비디오 잡 실행 함수 (병렬 처리 적용)
# -----------------------------------------------------------------------------
def _run_video_job(
    job_id: str,
    clips: List[VideoClip],
    duration: str,
    cfg_scale: float,
    mode: Optional[str],
    target_total_sec: Optional[float],
    include_intro_outro: Optional[bool],
    intro_url: Optional[str] = None,
    outro_url: Optional[str] = None
):
    try:
        # 1. 작업 초기화
        with video_jobs_lock:
            video_jobs[job_id] = {
                "status": "RUNNING",
                "message": "Preparing clips...",
                "progress": 1,
                "created_at": time.time()
            }

        out_dir = Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)

        # -------------------------------------------------------------
        # 2. 시간 설정 (2.5초 고정)
        # -------------------------------------------------------------
        per_clip_sec = 2.5 
        
        # -------------------------------------------------------------
        # 3. 실행 계획(Plan) 생성
        # -------------------------------------------------------------
        plan = []
        
        # [Intro]
        if include_intro_outro and intro_url:
            plan.append({"kind": "still", "type": "intro", "url": intro_url, "dur": 2.0})

        # [Main Clips]
        for i, clip in enumerate(clips):
            plan.append({
                "kind": "kling",
                "type": "scene",
                "url": clip.url,
                # [변경] preset 대신 motion, effect 전달
                "motion": clip.motion,
                "effect": clip.effect,
                "dur": per_clip_sec
            })

        # [Outro]
        if include_intro_outro and outro_url:
            plan.append({"kind": "still", "type": "outro", "url": outro_url, "dur": 2.0})

        total_steps = len(plan)
        
        # -------------------------------------------------------------
        # 4. 병렬 실행 (Parallel Execution) - 핵심 변경 사항
        # -------------------------------------------------------------
        print(f"🚀 [VideoJob] Starting parallel generation for {total_steps} clips (Max 5)...", flush=True)
        
        completed_counter = [0]
        completed_counter_lock = threading.Lock()
        future_map = {}
        generated_results = [None] * total_steps # 결과 순서 보장용 리스트

        # ThreadPoolExecutor를 사용하여 최대 5개 동시 실행
        with ThreadPoolExecutor(max_workers=5) as executor:
            for idx, item in enumerate(plan):
                # 개별 작업 제출
                future = executor.submit(
                    process_single_clip, 
                    idx, item, job_id, out_dir, total_steps, 
                    completed_counter_lock, completed_counter
                )
                future_map[future] = idx
            
            # 완료되는 대로 결과 수집 (에러 체크 포함)
            for future in as_completed(future_map):
                idx, path = future.result() # 에러 발생 시 여기서 raise됨
                generated_results[idx] = path # 원래 순서(index) 자리에 저장

        # None 체크 (혹시 모를 누락 방지)
        generated_paths = [p for p in generated_results if p is not None]

        # -------------------------------------------------------------
        # 5. 병합 (Concat)
        # -------------------------------------------------------------
        if not generated_paths:
            raise RuntimeError("No clips generated.")

        with video_jobs_lock:
             video_jobs[job_id]["message"] = "Stitching video..."
             video_jobs[job_id]["progress"] = 90

        # 정규화 (해상도 통일 & 레터박스)
        normalized_paths = []
        for i, p in enumerate(generated_paths):
            norm = out_dir / f"v_norm_{job_id}_{i}.mp4"
            # 1080x1920 세로형 기준
            _ffmpeg_normalize_to(p, norm, 1080, 1920, VIDEO_TARGET_FPS)
            normalized_paths.append(norm)

        # 최종 합치기
        list_file = out_dir / f"list_{job_id}.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in normalized_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")

        final_path = out_dir / f"final_{job_id}.mp4"
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(final_path)
        ]
        _run_ffmpeg(cmd)

        # 완료 처리
        result_url = f"/outputs/{final_path.name}"
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "COMPLETED"
            video_jobs[job_id]["message"] = "Done!"
            video_jobs[job_id]["progress"] = 100
            video_jobs[job_id]["result_url"] = result_url
            
        print(f"✅ [VideoJob] Finished: {result_url}", flush=True)

    except Exception as e:
        print(f"🔥 Job {job_id} Critical Error: {e}")
        traceback.print_exc()
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "FAILED"
            video_jobs[job_id]["error"] = str(e)
            video_jobs[job_id]["message"] = "Failed during generation."

@app.post("/video-mvp/create")
async def video_mvp_create(req: VideoCreateRequest):
    print(f"Video Request: Intro={req.intro_url}, Outro={req.outro_url}", flush=True)
    job_id = uuid.uuid4().hex
    with video_jobs_lock:
        video_jobs[job_id] = {
            "status": "QUEUED",
            "message": "Queued",
            "progress": 0,
            "result_url": None,
            "error": None,
        }
    
    # [중요] 스레드 실행 시 인자 전달 확인
    video_executor.submit(
        _run_video_job, 
        job_id, 
        req.clips, 
        req.duration, 
        req.cfg_scale, 
        req.mode, 
        req.target_total_sec, 
        req.include_intro_outro,
        req.intro_url, # 전달
        req.outro_url  # 전달
    )
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