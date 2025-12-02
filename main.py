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

# ---------------------------------------------------------
# 1. 환경 설정 및 초기화
# ---------------------------------------------------------
load_dotenv()
NANOBANANA_API_KEY = os.getenv("NANOBANANA_API_KEY")
MAGNIFIC_API_KEY = os.getenv("MAGNIFIC_API_KEY")
MAGNIFIC_ENDPOINT = os.getenv("MAGNIFIC_ENDPOINT", "https://api.freepik.com/v1/ai/image-upscaler")

# [모델 설정]
MODEL_NAME = 'gemini-3-pro-image-preview'

# ... (import 부분 생략) ...

if NANOBANANA_API_KEY:
    genai.configure(api_key=NANOBANANA_API_KEY)

# [수정됨] 폴더를 먼저 만들어야 에러가 안 납니다! (위치 이동)
os.makedirs("outputs", exist_ok=True)
os.makedirs("assets", exist_ok=True)
os.makedirs("static", exist_ok=True) # 혹시 모르니 static도 추가

app = FastAPI()

# [수정됨] 폴더가 이미 만들어진 상태에서 연결(mount)
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

os.makedirs("outputs", exist_ok=True)
os.makedirs("assets", exist_ok=True)

TOTAL_TIMEOUT_LIMIT = 180

# ---------------------------------------------------------
# 2. 라우트
# ---------------------------------------------------------
@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

@app.get("/room-types")
async def get_room_types():
    return JSONResponse(content=list(ROOM_STYLES.keys()))

@app.get("/styles/{room_type}")
async def get_styles_for_room(room_type: str):
    if room_type in ROOM_STYLES:
        return JSONResponse(content=ROOM_STYLES[room_type])
    return JSONResponse(content=[], status_code=404)

# ---------------------------------------------------------
# 3. 핵심 함수들
# ---------------------------------------------------------
def standardize_image(image_path, output_path=None):
    try:
        if output_path is None: output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB': img = img.convert('RGB')
            img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            base, _ = os.path.splitext(output_path)
            new_output_path = f"{base}.jpg"
            img.save(new_output_path, "JPEG", quality=95)
            return new_output_path
    except Exception as e:
        print(f"!! 표준화 실패: {e}", flush=True)
        return image_path

def generate_empty_room(image_path, start_time):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    print(f"\n--- [Stage 1] 빈 방 생성 시작 ({MODEL_NAME}) ---", flush=True)
    try:
        img = Image.open(image_path)
        prompt = (
            "IMAGE EDITING TASK (STRICT):\n"
            "Create a photorealistic image of this room but completely EMPTY.\n\n"
            
            "ACTIONS:\n"
            "1. REMOVE ALL furniture, rugs, decor, and lighting.\n"
            "2. REMOVE ALL window treatments (curtains, blinds, shades). Show bare windows/glass.\n"
            "3. KEEP the original floor material, wall color, ceiling structure, and windows EXACTLY as they are.\n"
            "4. IN-PAINT the removed areas seamlessly.\n\n"
            
            "OUTPUT RULE: Return ONLY the generated image. Do NOT output any text."
        )
        model = genai.GenerativeModel(MODEL_NAME)
        remaining = max(10, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        response = model.generate_content(
            [prompt, img], 
            request_options={'timeout': remaining},
            safety_settings=safety_settings
        )
        
        if response.parts:
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    print(">> [성공] 빈 방 이미지 생성됨!", flush=True)
                    unique_id = uuid.uuid4().hex[:8]
                    timestamp = int(time.time())
                    filename = f"empty_{timestamp}_{unique_id}.jpg"
                    output_path = os.path.join("outputs", filename)
                    with open(output_path, 'wb') as f: f.write(part.inline_data.data)
                    return standardize_image(output_path)
        
        print(">> [실패] 이미지가 생성되지 않았습니다.", flush=True)
        try:
            if response.text:
                print(f"   [모델 답변]: {response.text}", flush=True)
        except: pass
        return image_path 
    except Exception as e:
        print(f"!! Stage 1 시스템 에러: {e}", flush=True)
        return image_path

def generate_furnished_room(room_path, style_config, reference_image_path=None, start_time=0):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return room_path
    print(f"\n--- [Stage 2] 가구 배치 (Perspective Match 모드) ---", flush=True)
    try:
        room_img = Image.open(room_path)
        
        # [프롬프트] 텍스트 무시 + 3D 재배치 + 조명 4000K + 커튼
        prompt = (
            "IMAGE GENERATION TASK (Virtual Staging):\n"
            "Furnish the empty room using the furniture styles shown in the Moodboard.\n\n"
            
            "<CRITICAL: DO NOT COPY PASTE>\n"
            "1. RE-ARRANGE: Do NOT copy the layout or composition of the moodboard. You must place the furniture into the room's 3D space anew.\n"
            "2. NO TEXT LABELS: The moodboard contains text (e.g., 'sofa x 1'). IGNORE IT. Do NOT write any text in the final image.\n"
            "3. REMOVE BACKGROUND: Do NOT paste the white background of the moodboard. Only extract the furniture items.\n\n"
            
            "<LIGHTING INSTRUCTION: TURN ON ALL LIGHTS>\n"
            "1. ACTIVATE LIGHTING: Identify items labeled as 'pendant lighting', 'floor lighting', 'table lighting', or 'wall lighting' in the Moodboard.\n"
            "2. STATE: All identified lighting fixtures MUST be TURNED ON and emitting light.\n"
            "3. COLOR TEMPERATURE: Use a warm 4000K light color for a cozy atmosphere.\n"
            "4. EMISSIVE MATERIAL: The light bulbs/shades must look bright and glowing (Emissive).\n"
            "5. AMBIENT GLOW: Ensure the lights cast a soft, warm glow on the surrounding walls and floor.\n\n"
            
            "<MANDATORY WINDOW TREATMENT>\n"
            "- Install pure WHITE CHIFFON CURTAINS on all windows.\n"
            "- They must be SHEER (90% transparency), allowing natural light.\n\n"
            
            "<DESIGN INSTRUCTIONS>\n"
            "1. PERSPECTIVE MATCH: Align the furniture with the floor grid and vanishing points of the empty room.\n"
            "2. PLACEMENT: Place the furniture (Sofa, Rug, Tables) on the existing floor plane realistically.\n"
            "3. SCALE: Furniture size must be realistic relative to the room height.\n\n"
            
            "OUTPUT RULE: Return ONLY the generated interior image. No text, no moodboard layout."
        )
        
        input_content = [prompt, "Background Empty Room:", room_img]
        if reference_image_path:
            try:
                ref_img = Image.open(reference_image_path)
                if ref_img.width > 2048 or ref_img.height > 2048: ref_img.thumbnail((2048, 2048))
                input_content.append("Furniture Reference (Moodboard):")
                input_content.append(ref_img)
            except Exception as e:
                print(f"   ! 무드보드 로드 에러 (무시하고 진행): {e}", flush=True)
        
        model = genai.GenerativeModel(MODEL_NAME)
        remaining = max(30, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        response = model.generate_content(
            input_content, 
            request_options={'timeout': remaining},
            safety_settings=safety_settings
        )
        
        if response.parts:
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    print(">> [성공] 가구 배치 완료", flush=True)
                    timestamp = int(time.time())
                    filename = f"result_{timestamp}_{unique_id}.jpg"
                    output_path = os.path.join("outputs", filename)
                    with open(output_path, 'wb') as f: f.write(part.inline_data.data)
                    return standardize_image(output_path)
        print(">> [실패] 가구 배치 실패.", flush=True)
        try:
            if response.text:
                print(f"   [모델 답변]: {response.text}", flush=True)
        except: pass
        return room_path
    except Exception as e:
        print(f"!! Stage 2 에러: {e}", flush=True)
        return room_path

def call_magnific_api(image_path, start_time):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    print("\n--- [Stage 3] 업스케일링 시도 ---", flush=True)
    if not MAGNIFIC_API_KEY or "your_" in MAGNIFIC_API_KEY:
         print(">> [SKIP] API 키 없음.", flush=True)
         return image_path
    try:
        with open(image_path, "rb") as img_file:
            base64_string = base64.b64encode(img_file.read()).decode('utf-8')
        payload = {
            "image": base64_string, "scale_factor": "2x", "optimized_for": "standard",
            "prompt": "high quality, 4k, realistic interior, highly detailed, photorealistic",
            "creativity": 2, "hdr": 4, "resemblance": 4, "fractality": 3, "engine": "automatic"
        }
        headers = { "x-freepik-api-key": MAGNIFIC_API_KEY, "Content-Type": "application/json", "Accept": "application/json" }
        print(">> API 요청 전송...", flush=True)
        response = requests.post(MAGNIFIC_ENDPOINT, json=payload, headers=headers)
        if response.status_code != 200:
            print(f"!! [API 오류] {response.status_code}: {response.text}", flush=True)
            return image_path
        result_json = response.json()
        if "data" in result_json and "generated" in result_json["data"] and len(result_json["data"]["generated"]) > 0:
            return download_image(result_json["data"]["generated"][0])
        elif "data" in result_json and "task_id" in result_json["data"]:
            task_id = result_json["data"]["task_id"]
            print(f">> 작업 예약됨 (Task ID: {task_id}). 대기 중...", end="", flush=True)
            while time.time() - start_time < TOTAL_TIMEOUT_LIMIT:
                time.sleep(2)
                print(".", end="", flush=True)
                status_res = requests.get(f"{MAGNIFIC_ENDPOINT}/{task_id}", headers=headers)
                if status_res.status_code == 200:
                    s_data = status_res.json()
                    if s_data.get("data", {}).get("status") == "COMPLETED":
                        print("\n>> 작업 완료!", flush=True)
                        return download_image(s_data["data"]["generated"][0])
                    elif s_data.get("data", {}).get("status") == "FAILED":
                        print("\n!! [오류] 실패.", flush=True)
                        return image_path
            print("\n!! [시간 초과] 업스케일링 중단.", flush=True)
            return image_path
        else: return image_path
    except Exception as e:
        print(f"\n!! [시스템 에러] {e}", flush=True)
        return image_path

def download_image(url):
    try:
        img_response = requests.get(url)
        if img_response.status_code == 200:
            unique_id = uuid.uuid4().hex[:8]
            timestamp = int(time.time())
            filename = f"magnific_{timestamp}_{unique_id}.jpg"
            path = os.path.join("outputs", filename)
            with open(path, "wb") as f: f.write(img_response.content)
            print(f">> [성공] 저장됨: {filename}", flush=True)
            return standardize_image(path)
        return None
    except: return None

@app.post("/render")
def render_room(file: UploadFile = File(...), room: str = Form(...), style: str = Form(...), variant: str = Form(...)):
    full_style = f"{room}-{style}-{variant}"
    print(f"\n=== 요청 시작: {full_style} (Room: {room}, Style: {style}, Variant: {variant}) ===", flush=True)
    start_time = time.time()
    
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
    raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{safe_name}")
    with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    
    std_path = standardize_image(raw_path)
    step1_img = generate_empty_room(std_path, start_time)
    
    # [스마트 에셋 탐색]
    ref_path = None
    safe_room = room.lower().replace(" ", "")
    safe_style = style.lower().replace(" ", "-").replace("_", "-")
    target_dir = os.path.join("assets", safe_room, safe_style)
    print(f">> [Moodboard] 에셋 폴더 탐색: {target_dir}", flush=True)
    if os.path.exists(target_dir):
        files = sorted(os.listdir(target_dir))
        for f in files:
            if not f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')): continue
            numbers = re.findall(r'\d+', f)
            if variant in numbers:
                ref_path = os.path.join(target_dir, f)
                print(f">> [Moodboard] ✅ 파일 찾음 (번호 {variant}): {f}", flush=True)
                break
        if ref_path is None and len(files) > 0:
            ref_path = os.path.join(target_dir, files[0])
            print(f">> [Moodboard] ⚠️ 번호 일치 파일 없음. 대체 파일 사용: {files[0]}", flush=True)
    else:
        print(f">> [Moodboard] ❌ 폴더를 찾을 수 없습니다: {target_dir}", flush=True)
    if ref_path is None: print(">> [Moodboard] ❌ 경고: 에셋 찾기 실패 (AI 임의 생성)", flush=True)
    
    step2_img = generate_furnished_room(step1_img, STYLES.get(style, STYLES.get("Modern")), ref_path, start_time)
    final_img = call_magnific_api(step2_img, start_time)
    if final_img is None: final_img = step2_img
    
    elapsed = time.time() - start_time
    print(f"=== 총 소요 시간: {elapsed:.1f}초 ===", flush=True)
    
    # [핵심 수정] empty_room_url 포함
    return JSONResponse(content={
        "original_url": f"/outputs/{os.path.basename(std_path)}",
        "empty_room_url": f"/outputs/{os.path.basename(step1_img)}", # 여기가 빠져있었음!
        "result_url": f"/outputs/{os.path.basename(final_img)}",
        "message": "Complete" if elapsed <= TOTAL_TIMEOUT_LIMIT else "Timeout Partial Result"
    })

if __name__ == "__main__":
    import uvicorn
    try:
        print("🚀 서버를 시작합니다... (http://localhost:8001)", flush=True)
        print("💡 안정 모드: 서버가 꺼지지 않도록 자동 새로고침(Reload)을 껐습니다.", flush=True)
        uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
    except KeyboardInterrupt:
        print("\n⛔ 서버를 종료합니다.")
    except Exception as e:
        print(f"\n❌ 서버 실행 중 치명적 오류 발생: {e}")