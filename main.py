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
from concurrent.futures import ThreadPoolExecutor # 병렬 처리용
from pydantic import BaseModel
import gc 

# ---------------------------------------------------------
# 1. 환경 설정 및 초기화
# ---------------------------------------------------------
load_dotenv()

API_KEY_POOL = []
i = 1
while True:
    key = os.getenv(f"NANOBANANA_API_KEY_{i}") 
    if not key:
        key = os.getenv(f"NANOBANANA_API_KEY{i}")
        if not key:
            break
    API_KEY_POOL.append(key)
    i += 1

if not API_KEY_POOL:
    single_key = os.getenv("NANOBANANA_API_KEY")
    if single_key:
        API_KEY_POOL.append(single_key)

print(f"✅ 로드된 나노바나나 API 키 개수: {len(API_KEY_POOL)}개")

MAGNIFIC_API_KEY = os.getenv("MAGNIFIC_API_KEY")
MAGNIFIC_ENDPOINT = os.getenv("MAGNIFIC_ENDPOINT", "https://api.freepik.com/v1/ai/image-upscaler")
MODEL_NAME = 'gemini-3-pro-image-preview' 

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

# [설정] 3장 생성을 위해 시간 넉넉히
TOTAL_TIMEOUT_LIMIT = 300 

# ---------------------------------------------------------
# [NEW] 스마트 키 관리자 (할당량 초과 시 '잠시 열외' 시스템)
# ---------------------------------------------------------
QUOTA_EXCEEDED_KEYS = set()

def call_gemini_with_failover(model_name, contents, request_options, safety_settings, system_instruction=None):
    """
    [요구사항 반영]
    1. 원인 파악: 에러 발생 시 원인 분석
    2. 조치: 할당량/과부하 에러 -> 해당 키 Lock (QUOTA_EXCEEDED_KEYS에 추가)
            기타 에러 -> Lock 하지 않음
    3. 재시도: 살아있는 다른 키로 즉시 재시도
    """
    global API_KEY_POOL, QUOTA_EXCEEDED_KEYS
    
    # 내부적으로 키를 바꿔가며 시도할 횟수 (키 개수만큼)
    max_retries = len(API_KEY_POOL) + 1
    
    for attempt in range(max_retries):
        # 1. 사용 가능한 키 필터링 (락 걸린 키 제외)
        available_keys = [k for k in API_KEY_POOL if k not in QUOTA_EXCEEDED_KEYS]
        
        # 만약 다 죽었으면 -> 락 초기화 (한 바퀴 돌았으므로 다시 기회 부여)
        if not available_keys:
            print("🔄 [System] 모든 키가 락(Lock) 상태입니다. 락을 해제하고 다시 시작합니다.", flush=True)
            QUOTA_EXCEEDED_KEYS.clear()
            available_keys = list(API_KEY_POOL)
            time.sleep(1)

        # 2. 다음 키 선택 (랜덤으로 선택하여 병렬 처리 충돌 방지)
        current_key = random.choice(available_keys)
        masked_key = current_key[-4:]

        try:
            genai.configure(api_key=current_key)
            if system_instruction:
                model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
            else:
                model = genai.GenerativeModel(model_name)
            
            # API 호출
            response = model.generate_content(
                contents, 
                request_options=request_options,
                safety_settings=safety_settings
            )
            return response # 성공 시 반환

        except Exception as e:
            error_msg = str(e)
            
            # [요구사항] 할당량(429)이나 과부하 관련 에러인가?
            if "429" in error_msg or "403" in error_msg or "Quota" in error_msg or "limit" in error_msg:
                print(f"📉 [Lock] Key(...{masked_key}) 할당량 초과. 한 바퀴 돌 동안 잠급니다.", flush=True)
                QUOTA_EXCEEDED_KEYS.add(current_key) # 락 걸기
            else:
                # [요구사항] 기타 에러라면 락 하지 않음
                print(f"⚠️ [Error] Key(...{masked_key}) 단순 에러(락 안함): {error_msg}", flush=True)
            
            # 다음 키로 재시도를 위해 loop continue
            time.sleep(0.5)

    print("❌ [Fatal] 모든 키로 시도했으나 API 호출 실패.")
    return None

# ---------------------------------------------------------
# 2. 핵심 함수들 (빈방 생성 로직 강화)
# ---------------------------------------------------------
def standardize_image(image_path, output_path=None):
    try:
        if output_path is None: output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB': img = img.convert('RGB')
            # 1024px로 리사이징 (메모리 절약)
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            base, _ = os.path.splitext(output_path)
            new_output_path = f"{base}.jpg"
            img.save(new_output_path, "JPEG", quality=85)
            return new_output_path
    except Exception as e:
        print(f"!! 표준화 실패: {e}", flush=True)
        return image_path

def generate_empty_room(image_path, unique_id, start_time):
    """
    [요구사항 반영]
    빈방 생성이 실패했다? -> 다음 키로 다시 생성해 (최대 3회)
    """
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    print(f"\n--- [Stage 1] 빈 방 생성 시작 ---", flush=True)
    
    img = Image.open(image_path)
    system_instruction = "You are an expert architectural AI. Your task is to perform structure-preserving image editing. You must output an image."
    
    prompt = (
        "IMAGE EDITING TASK (STRICT):\n"
        "Create a photorealistic image of this room but completely EMPTY.\n"
        "1. REMOVE ALL furniture, rugs, decor, and lighting.\n"
        "2. REMOVE ALL window treatments. Show bare windows/glass.\n"
        "3. KEEP the original floor material, wall color, ceiling structure EXACTLY as they are.\n"
        "4. IN-PAINT the removed areas seamlessly.\n"
        "OUTPUT RULE: Return ONLY the generated image."
    )
    
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    # [핵심] 최대 3회 재시도 루프
    max_stage_retries = 3
    
    for try_count in range(max_stage_retries):
        remaining = max(10, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        
        # API 호출 (여기서 이미 1차적으로 키 관리를 해줌)
        response = call_gemini_with_failover(
            MODEL_NAME, 
            [prompt, img], 
            request_options={'timeout': remaining},
            safety_settings=safety_settings,
            system_instruction=system_instruction
        )
        
        # 성공 여부 검증 (이미지가 진짜 나왔나?)
        if response and response.parts:
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    print(f">> [성공] 빈 방 이미지 생성됨! (시도 {try_count+1}회차)", flush=True)
                    timestamp = int(time.time())
                    filename = f"empty_{timestamp}_{unique_id}.jpg"
                    output_path = os.path.join("outputs", filename)
                    with open(output_path, 'wb') as f: f.write(part.inline_data.data)
                    return standardize_image(output_path)
        
        # 실패 시 처리
        print(f"⚠️ [Stage 1 실패] 이미지가 생성되지 않음. (시도 {try_count+1}/{max_stage_retries}) -> 재시도합니다.", flush=True)
        # API 호출 함수가 이미 '할당량 에러'면 키를 잠갔을 것이고, 
        # '단순 이미지 미생성'이면 키를 안 잠근 상태로 유지됩니다.
        # 다음 루프에서 call_gemini_with_failover가 호출될 때 '새로운 키'를 뽑아서 시도하게 됩니다.

    print(">> [최종 실패] 3번 시도했으나 빈 방 생성 불가.", flush=True)
    return image_path

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
    # [🚀 POWER UP] 3장 동시 생성 (병렬 처리)
    # ---------------------------------------------------------
    generated_results = []
    
    print(f"\n🚀 [Parallel] 3장 동시 생성 시작! (서버 업그레이드 적용됨)", flush=True)

    # 1. 개별 작업을 수행할 함수 정의 (내부 함수)
    def process_one_variant(index):
        sub_id = f"{unique_id}_v{index+1}"
        print(f"   ▶ [Variation {index+1}] 스타트!", flush=True)
        try:
            # 순서: 빈방 이미지 -> 가구 배치 (API 호출)
            result_path = generate_furnished_room(step1_img, STYLES.get(style, STYLES.get("Modern")), ref_path, sub_id, start_time)
            print(f"   ✅ [Variation {index+1}] 생성 완료!", flush=True)
            return f"/outputs/{os.path.basename(result_path)}"
        except Exception as e:
            print(f"   ❌ [Variation {index+1}] 실패: {e}", flush=True)
            return None

    # 2. 3개의 일꾼(Worker)을 동시에 투입
    with ThreadPoolExecutor(max_workers=3) as executor:
        # 작업 3개를 한꺼번에 던짐
        futures = [executor.submit(process_one_variant, i) for i in range(3)]
        
        # 끝나는 대로 결과 수집
        for future in futures:
            res = future.result()
            if res:
                generated_results.append(res)

    elapsed = time.time() - start_time
    print(f"=== [{unique_id}] 총 소요 시간: {elapsed:.1f}초 (병렬 처리) / 생성된 이미지: {len(generated_results)}장 ===", flush=True)
    
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
        uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, timeout_keep_alive=300)
    except KeyboardInterrupt:
        print("\n⛔ 서버를 종료합니다.")
    except Exception as e:
        print(f"\n❌ 서버 실행 중 치명적 오류 발생: {e}")
