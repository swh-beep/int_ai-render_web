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

def standardize_image(image_path, output_path=None, keep_ratio=False, force_landscape=False):
    try:
        if output_path is None: output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            
            # [수정] 투명 배경(RGBA) 처리: 흰색 소품이 흰 배경에 묻히는 것을 방지하기 위해 중립 그레이(#D2D2D2) 배경 사용
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
                # 밝은 가구와 어두운 가구 모두 대비가 잘 보이는 중립적인 회색 배경 생성
                background = Image.new("RGBA", img.size, (210, 210, 210, 255)) 
                img = Image.alpha_composite(background, img).convert("RGB")
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            width, height = img.size
            
            # [FIX] force_landscape가 True면 -> 무조건 16:9 (1920x1080) 설정
            if force_landscape:
                target_size = (1920, 1080)
                target_ratio = 16 / 9
            # 기존 로직 (자동 감지)
            elif width >= height:
                target_size = (1920, 1080)
                target_ratio = 16 / 9
            else:
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

# [최종 복구 및 업그레이드] 분석(Flash) -> 생성(Pro-Image) 2단계 파이프라인
# 구글 AI 스튜디오의 "Generative Reconstruction" 로직 이식
def generate_frontal_room_from_photos(photo_paths, unique_id, index):
    try:
        print(f"   [Frontal Gen] Step 1: Analyzing {len(photo_paths)} photos with Flash (Spatial Mapping)...", flush=True)
        
        # 1. 이미지 로드
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
        # [Step 1] Flash 모델로 "공간 구조 및 3D 매핑" 분석
        # AI 스튜디오의 "Comprehending Spatial Data" 단계를 수행
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
        
        # 분석 모델 호출
        analysis_res = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [analysis_prompt] + input_images, {'timeout': 45}, {})
        spatial_blueprint = analysis_res.text if (analysis_res and analysis_res.text) else "A modern living room with large windows and tiled floor."
        
        print(f"   [Frontal Gen] Step 2: Synthesizing Frontal View based on Spatial Blueprint...", flush=True)

        # ---------------------------------------------------------
        # [Step 2] Pro Image 모델로 "생성형 재구성(Generative Reconstruction)"
        # AI 스튜디오의 "Defining the Frontal View" & "Spatial Fidelity" 로직 이식
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

        # 이미지 생성 모델 호출
        # input_images를 함께 넣어주어 시각적 텍스처(Texture)를 참조하게 함
        content_list = [generation_prompt] + input_images
        
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
                    
                    # [유지] 표준화 함수 (에러 없이 호출)
                    final_path = standardize_image(out_path)
                    return f"/outputs/{os.path.basename(final_path)}"
        return None

    except Exception as e:
        print(f"!! Frontal Gen Error: {e}", flush=True)
        return None

# [NEW] 엔드포인트: 도면 업로드 대신 -> 그냥 사진들만 업로드
@app.post("/generate-frontal-view")
def generate_frontal_view_endpoint(
    input_photos: List[UploadFile] = File(...) 
):
    try:
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        print(f"\n=== [Frontal View Gen] Processing {len(input_photos)} photos ===", flush=True)

        # 1. 업로드된 사진들 저장
        saved_photo_paths = []
        for idx, photo in enumerate(input_photos):
            # 파일명 안전하게 처리
            safe_name = "".join([c for c in photo.filename if c.isalnum() or c in "._-"])
            path = os.path.join("outputs", f"src_{timestamp}_{unique_id}_{idx}_{safe_name}")
            
            with open(path, "wb") as buffer: 
                shutil.copyfileobj(photo.file, buffer)
            saved_photo_paths.append(path)
        
        generated_results = []
        
        # 2. 병렬 생성 (5장 시도)
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
        print(f"🔥🔥🔥 [Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)
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

# [수정] 원본 프롬프트 유지 + 비율 자동 감지 + 텍스트/여백 금지 + 무드보드 비율 무시 + 공간 제약 사항 추가
def generate_furnished_room(room_path, style_prompt, ref_path, unique_id, furniture_specs=None, room_dimensions=None, placement_instructions=None, start_time=0):
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

        # [NEW] 공간 제약 사항 컨텍스트 구성
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
            "You are provided with ACTUAL DIMENSIONS for both the Room and the Furniture. Do not guess. CALCULATE.\n"
            
            "1. **ROOM VOLUME ANCHOR:**\n"
            f"   - **ACTUAL ROOM DIMENSIONS:** {room_dimensions} (Width x Depth x Height)\n"
            "   - VISUALIZE this specific 3D bounding box first.\n"
            "   - **CONSTRAINT:** All furniture must physically fit within these specific bounds. If the room is narrow, the furniture must look tight. If spacious, it must look open.\n"
            
            "2. **FURNITURE-TO-ROOM RATIO (SCALE LOCK):**\n"
            "   - **Item Width vs Room Width:** Calculate the ratio based on the provided specs.\n"
            "     * Logic: (Furniture Width / Room Width) = % of Frame Occupancy.\n"
            "     * Example Rule: If a Sofa is 2800mm and Room is 3500mm, render the sofa occupying roughly **80% of the wall width**.\n"
            "   - **Item Height vs Ceiling Height:** Calculate vertical ratio.\n"
            "     * Logic: (Furniture Height / Room Height) = Vertical Position.\n"
            "     * Example Rule: A low console (700mm) in a 2400mm room is less than **1/3 of the wall height**.\n"
            
            "3. **DEPTH & FLOOR OCCUPANCY (DYNAMIC CALCULATION):**\n"
            "   - **Step A:** Extract the 'Depth' value from the Room Dimensions provided above.\n"
            "   - **Step B:** Extract the 'Depth' value of the Rug (or Main Furniture) from the specs list.\n"
            "   - **Step C:** Compare them. If the Rug Depth is > 50% of the Room Depth, it MUST dominate the floor visually.\n"
            "   - **Action:** Render the floor coverage strictly according to this calculated percentage. Do not default to a small doormat size if the specs say it is huge.\n"
            
            "4. **RELATIONAL SIZING (CROSS-CHECK):**\n"
            "   - Compare Item A vs Item B dimensions provided in the specs.\n"
            "   - If Item A is stated as W:1500 and Item B is W:2800, Item A MUST be rendered at roughly half the visual width of Item B.\n"
            "   - **STRICT PROHIBITION:** Do not resize items to match 'aesthetic templates'. Follow the NUMBERS strictly.\n\n"

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
        # [조립] 비율 고정 및 '무드보드 비율 무시' 명령 추가 (세로 무드보드 문제 해결)
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
    moodboard: UploadFile = File(None),
    dimensions: str = Form(""),
    placement: str = Form("")
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
                res = generate_furnished_room(step1_img, current_style_prompt, ref_path, sub_id, furniture_specs=furniture_specs_text, room_dimensions=dimensions, placement_instructions=placement, start_time=start_time)
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
                # B. [핵심 수정] 무드보드가 없으면? -> 메인 이미지 분석 대상을 설정!
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


# =========================
# Video MVP (Kling Image-to-Video via Freepik API)
# =========================
class VideoClip(BaseModel):
    url: str
    motion: str = "static"
    effect: str = "none"
    speed: float = 1.0  # [NEW] 기본값(사용자가 수정 가능)

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
        "-crf", "10",          # [수정] 18 -> 10 (초고화질)
        "-preset", "veryslow", # [수정] veryfast -> veryslow (화질 최우선)
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
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "10",          # [수정] 18 -> 10 (초고화질)
        "-preset", "veryslow", # [수정] veryfast -> veryslow (화질 최우선)
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
    # [수정] 페이드 효과 제거, 해상도/비율만 맞춤
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
        "-crf", "10",          # [수정] 18 -> 10
        "-preset", "veryslow", # [수정] veryfast -> veryslow
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
# =========================================================
# [NEW] 2-Step Video Logic (Source Gen -> Final Compile)
# =========================================================

# --- 1. Request Models (데이터 모델 정의) ---
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
    Step 1: 소스 생성 로직
    - Static & No Effect: FFmpeg로 즉시 변환 (Fast, Free)
    - Motion or Effect: Kling AI 호출 (Slow, Cost)
    """
    filename = f"source_{job_id}_{idx}.mp4"
    out_path = out_dir / filename
    
    # [최적화] 움직임도 없고, 효과도 없으면 -> 그냥 이미지 5초 영상으로 변환 (Kling X)
    if item.motion == "static" and item.effect == "none":
        print(f"🚀 [Clip {idx}] Static detected. Skipping Kling (Fast generation).", flush=True)
        temp_img = out_dir / f"temp_src_{job_id}_{idx}.png"
        try:
            # 1. 이미지 다운로드
            _download_to_path(item.url, temp_img)
            
            # [수정] 1080, 1920 (세로) 파라미터 확인
            _ffmpeg_image_to_video(
                temp_img, out_path, 
                5.0, 
                1080, 1920, # <--- 여기가 1080, 1920 이어야 함
                VIDEO_TARGET_FPS
            )
            return out_path
        except Exception as e:
            print(f"Static Gen Error: {e}")
            raise e
        finally:
            if temp_img.exists(): temp_img.unlink()

    # ---------------------------------------------------------
    # 그 외 (모션이나 이펙트가 있는 경우) -> Kling 호출
    # ---------------------------------------------------------
    print(f"🎥 [Clip {idx}] Kling AI Generating... ({item.motion}/{item.effect})", flush=True)
    
    prompts = _kling_prompts_dynamic(item.motion, item.effect)
    img_b64 = _image_url_to_b64(item.url)
    
    # 5초 생성 요청
    task_id = _freepik_kling_create_task(
        img_b64, prompts["prompt"], prompts["negative_prompt"], 
        "5", cfg_scale
    )
    
    # 폴링 대기
    video_url = _freepik_kling_poll(task_id, job_id, idx, 1)
    
    # 다운로드
    _download_to_path(video_url, out_path)
    
    return out_path

def _run_source_generation(job_id: str, items: List[SourceItem], cfg_scale: float):
    try:
        with video_jobs_lock:
            video_jobs[job_id] = {"status": "RUNNING", "message": "Initializing...", "progress": 0, "results": []}

        out_dir = Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)
        
        total_steps = len(items)
        results_map = [None] * total_steps # 순서 보장용
        
        # 병렬 실행 (최대 5개 동시)
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
                        # 웹에서 접근 가능한 경로로 저장
                        results_map[idx] = f"/outputs/{path.name}"
                except Exception as e:
                    print(f"Clip {idx} failed: {e}")
                    results_map[idx] = None # 실패 시 None
                
                completed_count += 1
                # 진행률 업데이트
                with video_jobs_lock:
                    video_jobs[job_id]["progress"] = int((completed_count / total_steps) * 100)
                    video_jobs[job_id]["message"] = f"Generated {completed_count}/{total_steps} clips"

        # 완료
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "COMPLETED"
            video_jobs[job_id]["results"] = results_map # 결과 리스트 반환
            video_jobs[job_id]["message"] = "Source generation complete."

    except Exception as e:
        print(f"Source Gen Critical Error: {e}")
        traceback.print_exc()
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "FAILED"
            video_jobs[job_id]["error"] = str(e)

# --- 3. Step 2: Final Compile (자르기/배속/병합) ---
def _run_final_compile(job_id: str, req: CompileRequest):
    try:
        with video_jobs_lock:
            video_jobs[job_id] = {"status": "RUNNING", "message": "Compiling...", "progress": 0}
            
        out_dir = Path("outputs")
        processed_paths = []
        
        total_clips = len(req.clips)
        
        # 1. 각 클립 가공 (Trim -> Speed -> Resize)
        for i, clip in enumerate(req.clips):
            if not clip.video_url: continue
            
            # 원본 파일 확보 (로컬에 없으면 다운로드)
            src_name = _safe_filename_from_url(clip.video_url)
            local_src = out_dir / src_name
            if not local_src.exists():
                _download_to_path(clip.video_url, local_src)
            
            final_path = out_dir / f"proc_{job_id}_{i}.mp4"
            
            # 파라미터 계산
            t_start = max(0.0, clip.trim_start)
            t_end = min(5.0, clip.trim_end)
            if t_end <= t_start: t_end = 5.0
            
            dur = t_end - t_start
            # 속도 안전장치 (0이면 1.0으로)
            speed = clip.speed if clip.speed > 0.1 else 1.0
            
            # FFmpeg 필터 구성:
            # 1. trim: 구간 자르기
            # 2. setpts: 속도 조절 ((PTS-STARTPTS)/speed)
            # 3. scale/crop: 해상도 강제 통일 (1080x1920 등 기존 설정 따름)
            # 4. setsar=1: 픽셀 비율 초기화 (병합 오류 방지)
            setpts = f"(PTS-STARTPTS)/{speed}"
            
# [수정] 1080x1920 세로형(9:16) 강제 적용
            vf = (
                f"trim=start={t_start}:duration={dur},setpts={setpts},"
                f"scale=1080:1920:force_original_aspect_ratio=increase," # 9:16 비율로 늘리고
                f"crop=1080:1920,setsar=1,fps={VIDEO_TARGET_FPS}"       # 중앙 크롭
            )
            
            cmd = [
                "ffmpeg", "-y", "-i", str(local_src),
                "-vf", vf, "-an", 
                "-c:v", "libx264", "-pix_fmt", "yuv420p", 
                "-preset", "veryslow", # [수정] veryfast -> veryslow
                "-crf", "10",          # [수정] 18 -> 10
                str(final_path)
            ]
            _run_ffmpeg(cmd)
            processed_paths.append(final_path)
            
            # 진행률 (0~80%)
            with video_jobs_lock:
                video_jobs[job_id]["progress"] = int(((i + 1) / total_clips) * 80)

        # 2. 병합 (Concat)
        if not processed_paths: raise RuntimeError("No clips to merge")
        
        list_file = out_dir / f"list_{job_id}.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in processed_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")
        
        final_out = out_dir / f"final_{job_id}.mp4"
        # Concat 실행
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
    
    # 백그라운드 스레드로 실행
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