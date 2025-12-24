import os
import time
import shutil
import base64
import uuid
import requests
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

# ---------------------------------------------------------
# 1. 환경 설정 및 초기화
# ---------------------------------------------------------
load_dotenv()

MODEL_NAME = 'gemini-3-pro-image-preview'

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
    max_retries = len(API_KEY_POOL) + 2
    
    for attempt in range(max_retries):
        available_keys = [k for k in API_KEY_POOL if k not in QUOTA_EXCEEDED_KEYS]
        if not available_keys:
            print("🔄 [System] 모든 키가 락 상태. 초기화 후 재시도.", flush=True)
            QUOTA_EXCEEDED_KEYS.clear()
            available_keys = list(API_KEY_POOL)
            time.sleep(1)

        current_key = random.choice(available_keys)
        masked_key = current_key[-4:]

        try:
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel(model_name, system_instruction=system_instruction) if system_instruction else genai.GenerativeModel(model_name)
            
            response = model.generate_content(contents, request_options=request_options, safety_settings=safety_settings)
            return response

        except Exception as e:
            error_msg = str(e)
            if any(x in error_msg for x in ["429", "403", "Quota", "limit"]):
                print(f"📉 [Lock] Key(...{masked_key}) 할당량 초과.", flush=True)
                QUOTA_EXCEEDED_KEYS.add(current_key)
            else:
                print(f"⚠️ [Error] Key(...{masked_key}) 에러: {error_msg}", flush=True)
            time.sleep(0.5)

    print("❌ [Fatal] 모든 키 시도 실패.", flush=True)
    return None

def standardize_image(image_path, output_path=None):
    try:
        if output_path is None: output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB': img = img.convert('RGB')
            
            width, height = img.size
            target_ratio = 16 / 9
            current_ratio = width / height

            if current_ratio > target_ratio:
                new_width = int(height * target_ratio)
                offset = (width - new_width) // 2
                img = img.crop((offset, 0, offset + new_width, height))
            else:
                new_height = int(width / target_ratio)
                offset = (height - new_height) // 2
                img = img.crop((0, offset, width, offset + new_height))

            img = img.resize((1920, 1080), Image.Resampling.LANCZOS)
            
            base, _ = os.path.splitext(output_path)
            new_output_path = f"{base}.jpg"
            img.save(new_output_path, "JPEG", quality=90)
            return new_output_path
    except Exception as e:
        print(f"!! 표준화 실패: {e}", flush=True)
        return image_path

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
                        filename = f"empty_{timestamp}_{unique_id}.jpg"
                        path = os.path.join("outputs", filename)
                        with open(path, 'wb') as f: f.write(part.inline_data.data)
                        return standardize_image(path)
            else:
                reason = response.candidates[0].finish_reason if response.candidates else "Unknown"
                print(f"⚠️ [Blocked] 안전 필터 차단 (Finish Reason: {reason})", flush=True)
        print(f"⚠️ [Retry] 시도 {try_count+1} 실패. 재시도...", flush=True)

    print(">> [실패] 빈 방 생성 불가. 원본 사용.", flush=True)
    return image_path

def generate_furnished_room(room_path, style_prompt, ref_path, unique_id, start_time=0):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return None
    try:
        room_img = Image.open(room_path)
        system_instruction = "You are an expert interior designer AI."
        
        prompt = (
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
            "4. **WINDOW TREATMENT (CURTAINS):** Add floor-to-ceiling curtains/drapes. They must be **OPEN and PULLED BACK** to the sides. Cover ONLY the outer 20-25% of the window width (leaving the center view completely visible). Use natural fabric with soft vertical folds.\n\n"

            "<CRITICAL: DIMENSIONAL TEXT ADHERENCE>\n"
            "1. **OCR & CONSTRAINTS:** Actively SCAN the 'Style Reference' image for any text indicating dimensions (e.g., '2400mm', 'W:200cm', '3-seater', '1800x900').\n"
            "2. **SCALE ENFORCEMENT:** If dimensions are present, YOU MUST calibrate the size of the generated furniture to match these specific measurements relative to the room's perspective.\n"
            "3. **LOGIC CHECK:** Do not generate furniture that contradicts the text (e.g., if text says '1-person chair', do not generate a '3-person sofa').\n\n"

            "<CRITICAL: PHOTOREALISTIC LIGHTING INTEGRATION>\n"
            "1. **GLOBAL ILLUMINATION:** Simulate how natural light from the window bounces off the floor and interacts with the furniture. The side of the furniture facing the window must be highlighted, while the opposite side has soft, natural shading.\n"
            "2. **TURN ON LIGHTS:** TURN ON ALL artificial light sources in the room, including ceiling lights, pendant lights, wall sconces, and floor lamps. natural white light (5000K).\n"
            "2. **SHADOW PHYSICS:** Generate 'Soft Shadows' that diffuse as they get further from the object. Shadows must exactly match the direction and intensity of the sunlight entering the room.\n"
            "3. **ATMOSPHERE:** Create a 'Sun-drenched' feel where the light wraps around the fabric/materials of the furniture (Subsurface Scattering), making it look soft and cozy, not like a 3D sticker.\n"
            "OUTPUT RULE: Return the original room image with furniture added, perfectly blended with the natural light."
        )
        
        content = [prompt, "Empty Room:", room_img]
        if ref_path:
            try:
                ref = Image.open(ref_path)
                ref.thumbnail((2048, 2048))
                content.extend(["Style Reference:", ref])
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
                    filename = f"result_{timestamp}_{unique_id}.jpg"
                    path = os.path.join("outputs", filename)
                    with open(path, 'wb') as f: f.write(part.inline_data.data)
                    return path
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
            "fractality": 0,
            "prompt": (
                "Professional interior photography, architectural digest style, "
                "shot on Phase One XF IQ4, 100mm lens, ISO 100, f/8, "
                "natural daylight coming from window, soft shadows, subtle film grain, "
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
            print(f"!! [응답 형식 오류] data 필드 없음: {data}", flush=True)
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
                        else:
                            print(f"\n!! [오류] 완료되었으나 이미지가 없음. 응답: {check.json()}", flush=True)
                            return image_path
                            
                    elif status == "FAILED": 
                        print(f" 실패. (Reason: {status_data})", flush=True)
                        return image_path
            return image_path

        elif "generated" in data["data"]:
             gen_list = data["data"]["generated"]
             if gen_list and len(gen_list) > 0:
                 return download_image(gen_list[0], unique_id) or image_path
             else:
                 print(f"!! [오류] 생성된 이미지 리스트가 비어있습니다. 응답: {data}", flush=True)
                 return image_path
                 
        print(f"!! [오류] 알 수 없는 응답 형식: {data}", flush=True)
        return image_path
        
    except Exception as e:
        print(f"\n!! [시스템 에러] {e}", flush=True)
        traceback.print_exc()
        return image_path

def download_image(url, unique_id):
    try:
        res = requests.get(url)
        if res.status_code == 200:
            timestamp = int(time.time())
            filename = f"magnific_{timestamp}_{unique_id}.jpg"
            path = os.path.join("outputs", filename)
            with open(path, "wb") as f: f.write(res.content)
            return standardize_image(path)
        return None
    except: return None

@app.get("/")
async def read_index(): return FileResponse("static/index.html")

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

# [수정] moodboard 파일 파라미터 추가
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
        print(f"\n=== 요청 시작 [{unique_id}] (Parallel) ===", flush=True)
        start_time = time.time()
        
        timestamp = int(time.time())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
        raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{safe_name}")
        with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
        
        std_path = standardize_image(raw_path)
        
        step1_img = generate_empty_room(std_path, unique_id, start_time, stage_name="Stage 1: Intermediate Clean")
        
        ref_path = None
        
        if style == "Customize" and moodboard:
            mb_name = "".join([c for c in moodboard.filename if c.isalnum() or c in "._-"])
            mb_path = os.path.join("outputs", f"mb_{timestamp}_{unique_id}_{mb_name}")
            with open(mb_path, "wb") as buffer: shutil.copyfileobj(moodboard.file, buffer)
            ref_path = mb_path
            print(f">> [Style: Customize] Custom Moodboard Used: {mb_path}", flush=True)
        else:
            target_dir = os.path.join("assets", room.lower().replace(" ", ""), style.lower().replace(" ", "-").replace("_", "-"))
            if os.path.exists(target_dir):
                files = sorted(os.listdir(target_dir))
                for f in files:
                    if re.search(rf"(?:^|[\D]){re.escape(variant)}(?:[\D]|$)", f):
                       ref_path = os.path.join(target_dir, f)
                       break
                if not ref_path and files: ref_path = os.path.join(target_dir, files[0])

        generated_results = []
        print(f"\n🚀 [Stage 2] 5장 동시 생성 시작 (Furnishing)!", flush=True)

        def process_one_variant(index):
            sub_id = f"{unique_id}_v{index+1}"
            print(f"   ▶ [Variation {index+1}] 스타트!", flush=True)
            try:
                current_style_prompt = STYLES.get(style, "Custom Moodboard Style" if style == "Customize" else STYLES.get("Modern", "Modern Style"))
                
                res = generate_furnished_room(step1_img, current_style_prompt, ref_path, sub_id, start_time)
                if res:
                    print(f"   ✅ [Variation {index+1}] 성공!", flush=True)
                    return f"/outputs/{os.path.basename(res)}"
            except Exception as e: print(f"   ❌ [Variation {index+1}] 에러: {e}", flush=True)
            return None

        # [수정: 동시성 개선] max_workers를 5 -> 6으로 증가시켜 조금 더 넉넉하게 처리
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(process_one_variant, i) for i in range(5)]
            for future in futures:
                res = future.result()
                if res: generated_results.append(res)
                gc.collect()

        print(f"=== [{unique_id}] 가구 배치 완료: {len(generated_results)}장 ===", flush=True)
        
        final_before_url = f"/outputs/{os.path.basename(step1_img)}"
        if generated_results:
            print(f"\n--- [Stage 3] 결과물 기반 Before 이미지 생성 시작 ---", flush=True)
            try:
                first_result_filename = os.path.basename(generated_results[0])
                first_result_path = os.path.join("outputs", first_result_filename)
                
                final_before_path = generate_empty_room(first_result_path, unique_id + "_final", start_time, stage_name="Stage 3: Final Before View")
                final_before_url = f"/outputs/{os.path.basename(final_before_path)}"
                print(">> [성공] 최종 비교용 Before 이미지 생성 완료", flush=True)
            except Exception as e:
                print(f"!! [경고] Step 3 실패, Step 1 이미지 사용: {e}", flush=True)

        if not generated_results: generated_results.append(f"/outputs/{os.path.basename(step1_img)}")

        return JSONResponse(content={
            "original_url": f"/outputs/{os.path.basename(std_path)}", 
            "empty_room_url": final_before_url,
            "result_url": generated_results[0], 
            "result_urls": generated_results, 
            "message": "Complete"
        })
    except Exception as e:
        print(f"\n🔥🔥🔥 [SERVER CRASH] {e}", flush=True)
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpscaleRequest(BaseModel): image_url: str

@app.post("/upscale")
def upscale_and_download(req: UpscaleRequest):
    try:
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        
        if not os.path.exists(local_path): 
            return JSONResponse(content={"error": "File not found"}, status_code=404)
        
        final_path = call_magnific_api(local_path, uuid.uuid4().hex[:8], time.time())
        is_failed = os.path.abspath(final_path) == os.path.abspath(local_path)
        
        response_data = {
            "upscaled_url": f"/outputs/{os.path.basename(final_path)}",
            "message": "Success"
        }
        
        if is_failed:
            response_data["warning"] = "업스케일링에 실패하여 원본 이미지를 다운로드합니다.\n(서버 로그를 확인해주세요)"
            
        return JSONResponse(content=response_data)
    except Exception as e: 
        return JSONResponse(content={"error": str(e)}, status_code=500)

# -----------------------------------------------------------------------------
# [수정됨] SHOT_STYLES 재배치 (1~3: 가로 유리 / 4~10: 세로 유리)
# -----------------------------------------------------------------------------
SHOT_STYLES = [
    # 1. [가로] 전체적인 배치와 공간감을 보여주는 쿼터뷰
    {
        "name": "Isometric Angle Context",
        "prompt": "FOCUS: The Furniture group layout.\nCOMPOSITION: High Angle View (approx 30 degrees). Wide shot showing the geometric relationship between furniture pieces within the room context.\nSTYLE: Modern, clean architectural view, horizontal composition."
    },
    # 2. [가로] 테이블 위의 오브제와 넓은 상판
    {
        "name": "Tabletop Context",
        "prompt": "FOCUS: Decorative objects on the Coffee Table, including the table edges.\nCOMPOSITION: Eye-level Medium Shot. Capture the spread of objects across the table's horizontal surface area. Do not crop the table too tightly.\nATMOSPHERE: Curated, editorial lifestyle look."
    },
    # 3. [가로] 소파나 긴 가구의 측면 라인
    {
        "name": "Side Profile View",
        "prompt": "FOCUS: The full side profile of the main furniture (Sofa or Long Bench).\nCOMPOSITION: Eye-level Side View (90 degrees). Capture the long horizontal lines and proportions of the furniture from the side. Minimalist and geometric."
    },
    # -----------------------------------------------------------
    # 여기서부터 세로(9:16) 비율로 생성됨
    # -----------------------------------------------------------
    # 4. [세로] 1인 체어의 수직 실루엣 강조
    {
        "name": "Solo Chair Silhouette",
        "prompt": "FOCUS: A single Lounge Chair or Armchair isolated in the frame.\nCOMPOSITION: Full Medium Shot (Portrait). Capture the vertical height, distinct outline, and curves of the chair back. Highlight the design silhouette top-to-bottom."
    },
    # 5. [세로] 천장에서 떨어지거나 서 있는 조명
    {
        "name": "Lighting & Atmosphere",
        "prompt": "FOCUS: The Pendant Light or Floor Lamp.\nCOMPOSITION: Low Angle looking up or Eye-level vertical shot. Emphasize the vertical line of the lamp cord or stand. Show how the light hangs in the space.\nLIGHTING: Pure White Daylight."
    },
    # 6. [세로] 가구 다리와 바닥의 연결
    {
        "name": "Structural Leg Detail",
        "prompt": "FOCUS: The vertical connection of the furniture leg to the body frame.\nANGLE: Low Angle (Ground level). Capture the height of the leg and its silhouette against the floor. Emphasize vertical structural elegance."
    },
    # 7. [세로] 암레스트와 쿠션의 층위
    {
        "name": "Fabric & Form Focus",
        "prompt": "FOCUS: The armrest and seat cushion stacking.\nCOMPOSITION: Medium Close-up (Portrait). Show the vertical volume and shape of the furniture arm along with the fabric texture falling downwards.\nLIGHTING: Soft side-lighting."
    },
    # 8. [세로] 소파 코너의 깊이감
    {
        "name": "Sofa Corner Styling",
        "prompt": "FOCUS: The corner section of the Sofa.\nCOMPOSITION: Vertical Medium Shot. Show the angle where the backrest meets the seat. Capture the cozy, enclosed vertical depth of the seating area."
    },
    # 9. [세로] 모서리 마감 라인
    {
        "name": "Edge Profile",
        "prompt": "FOCUS: The vertical profile line of a furniture edge.\nCOMPOSITION: Close-up Portrait. Follow the vertical line of the edge from top to bottom. Show the thickness and craftsmanship."
    },
    # 10. [세로] 빛이 떨어지는 느낌
    {
        "name": "Sunlight on Form",
        "prompt": "FOCUS: A section of furniture bathed in vertical sunlight.\nCOMPOSITION: Medium Shot. Capture the light falling from the top down onto the fabric or material. Show the play of light and shadow vertically."
    }
]

def generate_detail_view(original_image_path, style_config, unique_id, index):
    try:
        img = Image.open(original_image_path)
        
        # [수정] 프롬프트로 비율 제어 (1~3: 16:9, 4~10: 9:16)
        target_aspect_ratio_text = "16:9"
        if 4 <= index <= 10:
            target_aspect_ratio_text = "9:16"
            
        final_prompt = (
            "TASK: Create a photorealistic interior detail shot based on the provided room image.\n"
            "STRICT CONSTRAINT: You must generate a close-up view of an object existing in the input image. Do not invent new furniture.\n\n"
            "<GLOBAL RULE: DISTANCE & FORM>\n"
            "1. DO NOT ZOOM IN TOO MUCH. The 'Shape' and 'Silhouette' of the furniture are the most important elements.\n"
            "2. Keep the camera at a 'Medium Shot' distance to show the furniture's volume and structure.\n"
            "3. Avoid cutting off the edges of the main subject.\n\n"
            f"<PHOTOGRAPHY STYLE: {style_config['name']}>\n"
            f"{style_config['prompt']}\n\n"
            # [핵심] 프롬프트 텍스트에 비율 명시 (Config 사용 안 함)
            f"OUTPUT ASPECT RATIO: {target_aspect_ratio_text}\n" 
            "OUTPUT RULE: Return a high-quality, editorial composition matching the description."
        )
        
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        content = [final_prompt, "Original Room Context (Source):", img]
        
        # [수정] GenerationConfig 없이 호출 (비율은 프롬프트가 해결)
        response = call_gemini_with_failover(MODEL_NAME, content, {'timeout': 45}, safety_settings)
        
        if response and hasattr(response, 'candidates') and response.candidates:
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    safe_style_name = style_config['name'].replace(" ", "")
                    filename = f"detail_{timestamp}_{unique_id}_{index}_{safe_style_name}.jpg"
                    path = os.path.join("outputs", filename)
                    with open(path, 'wb') as f: f.write(part.inline_data.data)
                    return f"/outputs/{filename}"
        return None
    except Exception as e:
        print(f"!! Detail Generation Error: {e}")
        return None

class DetailRequest(BaseModel):
    image_url: str

class RegenerateDetailRequest(BaseModel):
    original_image_url: str
    style_index: int

@app.post("/regenerate-single-detail")
def regenerate_single_detail(req: RegenerateDetailRequest):
    try:
        filename = os.path.basename(req.original_image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "Original image not found"}, status_code=404)
        
        if req.style_index < 0 or req.style_index >= len(SHOT_STYLES):
            return JSONResponse(content={"error": "Invalid style index"}, status_code=400)

        unique_id = uuid.uuid4().hex[:6]
        style = SHOT_STYLES[req.style_index]
        
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
        print(f"\n=== [Detail View] 요청 시작 ({unique_id}) - 고정 스타일 모드 ===", flush=True)

        generated_results = []
        print(f"🚀 Generating {len(SHOT_STYLES)} Style Shots...", flush=True)
        
        # [수정: 동시성 개선] 워커 수를 3 -> 6으로 증가시켜 딜레이 감소
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = []
            for i, style in enumerate(SHOT_STYLES):
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

# -----------------------------------------------------------------------------
# [NEW] Moodboard Generator Feature
# -----------------------------------------------------------------------------

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

def generate_moodboard_logic(image_path, unique_id, index):
    try:
        img = Image.open(image_path)
        
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        response = call_gemini_with_failover(MODEL_NAME, [MOODBOARD_SYSTEM_PROMPT, img], {'timeout': 45}, safety_settings)
        
        if response and hasattr(response, 'candidates') and response.candidates:
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    filename = f"gen_mb_{timestamp}_{unique_id}_{index}.jpg"
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
        
        generated_results = []
        
        # [수정: 동시성 개선] 무드보드 생성도 6개씩 병렬 처리
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(generate_moodboard_logic, raw_path, unique_id, i+1) for i in range(5)]
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, log_level="info")