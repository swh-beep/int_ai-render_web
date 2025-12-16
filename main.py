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
from pydantic import BaseModel
import re
import traceback

# ---------------------------------------------------------
# 1. 환경 설정 및 초기화
# ---------------------------------------------------------
load_dotenv()

# [KEY ROTATION SYSTEM] API 키 풀(Pool) 로드
# .env 파일이나 Render 환경변수에 NANOBANANA_API_KEY_1, _2, _3 ... 형태로 저장하세요.
API_KEY_POOL = []
i = 1
while True:
    # f"NANOBANANA_API_KEY_{i}" 로 수정 (언더바 추가)
    key = os.getenv(f"NANOBANANA_API_KEY_{i}") 
    if not key:
        # 혹시 언더바 없이 저장했을 수도 있으니 한 번 더 체크
        key = os.getenv(f"NANOBANANA_API_KEY{i}")
        if not key:
            break
    API_KEY_POOL.append(key)
    i += 1

# 만약 1, 2 형식이 없다면 기존 단일 키(NANOBANANA_API_KEY)를 사용
if not API_KEY_POOL:
    single_key = os.getenv("NANOBANANA_API_KEY")
    if single_key:
        API_KEY_POOL.append(single_key)

print(f"✅ 로드된 나노바나나 API 키 개수: {len(API_KEY_POOL)}개")

# 현재 사용 중인 키 인덱스 (서버가 켜져있는 동안 유지됨)
CURRENT_KEY_INDEX = 0

MAGNIFIC_API_KEY = os.getenv("MAGNIFIC_API_KEY")
MAGNIFIC_ENDPOINT = os.getenv("MAGNIFIC_ENDPOINT", "https://api.freepik.com/v1/ai/image-upscaler")

# [모델 설정] 
MODEL_NAME = 'gemini-3-pro-image-preview' 

# 초기 키 설정
if API_KEY_POOL:
    genai.configure(api_key=API_KEY_POOL[CURRENT_KEY_INDEX])
    print(f"🔑 초기 API 키 설정 완료: Key #{CURRENT_KEY_INDEX + 1}")

# [필수] 폴더 생성 (순서 중요)
os.makedirs("outputs", exist_ok=True)
os.makedirs("assets", exist_ok=True)
os.makedirs("static", exist_ok=True)

app = FastAPI()

# [필수] 정적 파일 연결
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
# [NEW] API Key Failover Logic (핵심 기능)
# ---------------------------------------------------------
def switch_to_next_key():
    """현재 키가 에러가 나면 다음 키로 변경 (끝까지 가면 다시 1번으로 순환)"""
    global CURRENT_KEY_INDEX
    
    # [수정] 나머지 연산자(%)를 사용하여 무한 순환 구현
    # 예: 키가 3개일 때 -> 0->1, 1->2, 2->0 (다시 처음으로)
    next_index = (CURRENT_KEY_INDEX + 1) % len(API_KEY_POOL)
    
    # 키 변경 적용
    CURRENT_KEY_INDEX = next_index
    new_key = API_KEY_POOL[CURRENT_KEY_INDEX]
    genai.configure(api_key=new_key)
    
    print(f"♻️ [Failover] API 키 변경됨! (Key #{CURRENT_KEY_INDEX + 1}번 키 사용 중)")
    return True
    
    # 키 변경 적용
    CURRENT_KEY_INDEX = next_index
    new_key = API_KEY_POOL[CURRENT_KEY_INDEX]
    genai.configure(api_key=new_key)
    print(f"♻️ [Failover] API 키 변경됨! (Key #{CURRENT_KEY_INDEX} -> Key #{CURRENT_KEY_INDEX + 1})")
    return True

def call_gemini_with_failover(model_name, contents, request_options, safety_settings, system_instruction=None):
    """
    [수정] model 객체 대신 model_name을 받아서, 
    시도할 때마다 새로운 키로 모델을 다시 로드하는 방식
    """
    global CURRENT_KEY_INDEX
    max_retries = len(API_KEY_POOL)
    if max_retries == 0: max_retries = 1
    
    attempt = 0
    
    while attempt < max_retries + 1: # 키 개수 + 1번 정도 여유 있게 시도
        try:
            # [핵심 변경] 매 시도마다 모델을 새로 생성해야 바뀐 키가 적용됨!
            # system_instruction이 있다면 포함해서 생성
            if system_instruction:
                current_model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
            else:
                current_model = genai.GenerativeModel(model_name)

            print(f"👉 [Try] Key #{CURRENT_KEY_INDEX + 1}로 요청 시도...", flush=True)

            response = current_model.generate_content(
                contents, 
                request_options=request_options,
                safety_settings=safety_settings
            )
            return response
            
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ [Error] Key #{CURRENT_KEY_INDEX + 1} 실패: {error_msg}", flush=True)
            
            # 429: Too Many Requests, 403: Quota Exceeded 등의 에러일 때 키 교체
            # (사실 모든 에러에 대해 교체해도 무방하지만, 명시적으로 로그 남김)
            if "429" in error_msg or "403" in error_msg or "Quota" in error_msg or "limit" in error_msg:
                print("📉 쿼터 초과 감지! 키 교체 진행합니다.")
            
            if switch_to_next_key():
                attempt += 1
                time.sleep(1) # 너무 빠른 재시도 방지
            else:
                print("❌ 더 이상 사용할 수 있는 키가 없습니다.")
                raise e
    
    return None

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

def generate_empty_room(image_path, unique_id, start_time):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    print(f"\n--- [Stage 1] 빈 방 생성 시작 ({MODEL_NAME}) ---", flush=True)
    try:
        img = Image.open(image_path)
        prompt = (
            "IMAGE EDITING TASK (STRICT):\n"
            "Create a photorealistic image of this room but completely EMPTY.\n\n"
            "ACTIONS:\n"
            "1. REMOVE ALL furniture, rugs, decor, and lighting.\n"
            "2. REMOVE ALL window treatments. Show bare windows/glass.\n"
            "3. KEEP the original floor material, wall color, ceiling structure EXACTLY as they are.\n"
            "4. IN-PAINT the removed areas seamlessly.\n\n"
            "OUTPUT RULE: Return ONLY the generated image."
        )
        
        remaining = max(10, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        # [변경] model 객체 대신 MODEL_NAME 문자열을 넘김
        response = call_gemini_with_failover(
            MODEL_NAME, # <--- 여기가 핵심
            [prompt, img],
            request_options={'timeout': remaining},
            safety_settings=safety_settings
        )
        
        if response and response.parts:
            # (기존 저장 로직 동일...)
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    print(">> [성공] 빈 방 이미지 생성됨!", flush=True)
                    timestamp = int(time.time())
                    filename = f"empty_{timestamp}_{unique_id}.jpg"
                    output_path = os.path.join("outputs", filename)
                    with open(output_path, 'wb') as f: f.write(part.inline_data.data)
                    return standardize_image(output_path)
        
        # (실패 처리 로직 동일...)
        print(">> [실패] 이미지가 생성되지 않았습니다.", flush=True)
        return image_path 
    except Exception as e:
        print(f"!! Stage 1 시스템 에러: {e}", flush=True)
        traceback.print_exc() # 에러 상세 출력
        return image_path

def generate_furnished_room(room_path, style_config, reference_image_path, unique_id, start_time=0):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return room_path
    print(f"\n--- [Stage 2] 가구 배치 ---", flush=True)
    try:
        room_img = Image.open(room_path)
        
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
            "3. COLOR TEMPERATURE: Use 4000K light color for a cozy atmosphere.\n"
            "4. EMISSIVE MATERIAL: The light bulbs/shades must look bright and glowing (Emissive).\n"
            "5. AMBIENT GLOW: Ensure the lights cast a soft, warm glow on the surrounding walls and floor.\n\n"
            
            "<MANDATORY WINDOW TREATMENT>\n"
            "- Install pure WHITE CHIFFON CURTAINS on all windows.\n"
            "- They must be SHEER (80% transparency), allowing natural light.\n\n"
            
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
        
# [변경] model 객체 대신 MODEL_NAME 문자열을 넘김
        response = call_gemini_with_failover(
            MODEL_NAME, # <--- 여기가 핵심
            input_content, 
            request_options={'timeout': remaining},
            safety_settings=safety_settings
        )
        
        if response and response.parts:
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    print(">> [성공] 가구 배치 완료", flush=True)
                    timestamp = int(time.time())
                    filename = f"result_{timestamp}_{unique_id}.jpg"
                    output_path = os.path.join("outputs", filename)
                    with open(output_path, 'wb') as f: f.write(part.inline_data.data)
                    return standardize_image(output_path)
        
        print(">> [실패] 가구 배치 실패.", flush=True)
        return room_path
    except Exception as e:
        print(f"!! Stage 2 에러: {e}", flush=True)
        traceback.print_exc()
        return room_path

def call_magnific_api(image_path, unique_id, start_time):
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
            return download_image(result_json["data"]["generated"][0], unique_id)
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
                        return download_image(s_data["data"]["generated"][0], unique_id)
                    elif s_data.get("data", {}).get("status") == "FAILED":
                        print("\n!! [오류] 실패.", flush=True)
                        return image_path
            print("\n!! [시간 초과] 업스케일링 중단.", flush=True)
            return image_path
        else: return image_path
    except Exception as e:
        print(f"\n!! [시스템 에러] {e}", flush=True)
        return image_path

def download_image(url, unique_id):
    try:
        img_response = requests.get(url)
        if img_response.status_code == 200:
            timestamp = int(time.time())
            filename = f"magnific_{timestamp}_{unique_id}.jpg"
            path = os.path.join("outputs", filename)
            with open(path, "wb") as f: f.write(img_response.content)
            print(f">> [성공] 저장됨: {filename}", flush=True)
            return standardize_image(path)
        return None
    except: return None

# ---------------------------------------------------------
# 4. 메인 엔드포인트
# ---------------------------------------------------------
@app.post("/render")
def render_room(file: UploadFile = File(...), room: str = Form(...), style: str = Form(...), variant: str = Form(...)):
    full_style = f"{room}-{style}-{variant}"
    unique_id = uuid.uuid4().hex[:8]
    
    print(f"\n=== 요청 시작 [{unique_id}]: {full_style} (3 Variations) ===", flush=True)
    start_time = time.time()
    
    # 1. 원본 저장 및 표준화
    timestamp = int(time.time())
    safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
    raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{safe_name}")
    
    with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    std_path = standardize_image(raw_path)
    
    # 2. 빈 방 만들기 (Stage 1) - 한 번만 실행하면 됨 (공통)
    step1_img = generate_empty_room(std_path, unique_id, start_time)
    
    # 3. 무드보드 에셋 찾기
    ref_path = None
    safe_room = room.lower().replace(" ", "")
    safe_style = style.lower().replace(" ", "-").replace("_", "-")
    target_dir = os.path.join("assets", safe_room, safe_style)
    
    if os.path.exists(target_dir):
        files = sorted(os.listdir(target_dir))
        for f in files:
            if variant in f: # 간단한 매칭
                ref_path = os.path.join(target_dir, f)
                break
        if not ref_path and files: ref_path = os.path.join(target_dir, files[0])
    
    # ---------------------------------------------------------
    # [변경점] 3장 생성 루프 (Parallel or Sequential)
    # Render 서버 부하를 고려해 순차적으로 3장 생성
    # ---------------------------------------------------------
    generated_results = []
    
    for i in range(3): # 3번 반복
        if time.time() - start_time > TOTAL_TIMEOUT_LIMIT - 30: 
            print("⏰ 시간 부족으로 추가 생성 중단")
            break
            
        print(f"\n🎨 [Variation {i+1}/3] 생성 중...", flush=True)
        # unique_id에 순번을 붙여서 파일명 구분
        sub_id = f"{unique_id}_v{i+1}"
        
        # Stage 2 생성 (Gemini)
        # 프롬프트에 약간의 변형을 주고 싶다면 generate_furnished_room 내부에서 랜덤성을 기대하거나
        # i 값을 넘겨서 프롬프트를 미세하게 조정할 수도 있음 (현재는 Gemini의 랜덤성에 의존)
        result_path = generate_furnished_room(step1_img, STYLES.get(style, STYLES.get("Modern")), ref_path, sub_id, start_time)
        
        # [중요] 3장 모두 업스케일링(Magnific)을 하면 시간이 너무 오래 걸림 (비용+시간 문제)
        # 전략: 우선 3장 모두 Gemini 결과물을 리스트에 담습니다.
        # 만약 꼭 고화질이 필요하면 첫 번째만 하거나, 나중에 선택된 것만 하는 API를 따로 파야 합니다.
        # 여기서는 시간 관계상 Gemini 결과물(Stage 2)을 바로 반환합니다.
        
        generated_results.append(f"/outputs/{os.path.basename(result_path)}")

    elapsed = time.time() - start_time
    print(f"=== [{unique_id}] 총 소요 시간: {elapsed:.1f}초 / 생성된 이미지: {len(generated_results)}장 ===", flush=True)
    
    # 결과가 하나도 없으면 원본이라도 넣음
    if not generated_results:
        generated_results.append(f"/outputs/{os.path.basename(step1_img)}")

    return JSONResponse(content={
        "original_url": f"/outputs/{os.path.basename(step1_img)}", 
        "empty_room_url": f"/outputs/{os.path.basename(step1_img)}", 
        "result_urls": generated_results, # [url1, url2, url3] 리스트 반환
        "message": "Complete"
    })
class UpscaleRequest(BaseModel):
    image_url: str

@app.post("/upscale")
def upscale_and_download(req: UpscaleRequest):
    try:
        # 클라이언트가 보낸 URL (/outputs/파일이름.jpg)에서 파일명만 추출
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "File not found"}, status_code=404)
            
        unique_id = uuid.uuid4().hex[:8]
        start_time = time.time() # 업스케일링을 위한 시간 카운트 새로 시작
        
        print(f"\n--- [Request] 개별 업스케일링 요청: {filename} ---", flush=True)
        
        # 기존에 있던 함수 그대로 재활용
        final_path = call_magnific_api(local_path, unique_id, start_time)
        
        # 결과 반환
        return JSONResponse(content={
            "upscaled_url": f"/outputs/{os.path.basename(final_path)}",
            "message": "Success"
        })
    except Exception as e:
        print(f"!! 업스케일링 에러: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)
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
