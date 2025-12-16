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
MODEL_NAME = 'gemini-2.0-flash' # 모델명 확인

# [설정] 3장 생성을 위해 시간 넉넉히
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

# ---------------------------------------------------------
# [NEW] 스마트 키 관리자 (할당량 초과 시 '잠시 열외' 시스템)
# ---------------------------------------------------------
QUOTA_EXCEEDED_KEYS = set()

def call_gemini_with_failover(model_name, contents, request_options, safety_settings, system_instruction=None):
    global API_KEY_POOL, QUOTA_EXCEEDED_KEYS
    
    max_retries = len(API_KEY_POOL) + 1
    
    for attempt in range(max_retries):
        available_keys = [k for k in API_KEY_POOL if k not in QUOTA_EXCEEDED_KEYS]
        
        if not available_keys:
            print("🔄 [System] 락 해제 및 재시작", flush=True)
            QUOTA_EXCEEDED_KEYS.clear()
            available_keys = list(API_KEY_POOL)
            time.sleep(1)

        current_key = random.choice(available_keys)
        masked_key = current_key[-4:]

        try:
            genai.configure(api_key=current_key)
            if system_instruction:
                model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
            else:
                model = genai.GenerativeModel(model_name)
            
            response = model.generate_content(
                contents, 
                request_options=request_options,
                safety_settings=safety_settings
            )
            
            # [추가] 즉시 검사: 구글이 입구컷 시켰는지 확인
            if response.prompt_feedback:
                if response.prompt_feedback.block_reason:
                    print(f"🚫 [Block] Key(...{masked_key}) 구글이 차단함! 사유: {response.prompt_feedback.block_reason}", flush=True)
                    # 이건 키 문제가 아니라 '내용' 문제이므로, 재시도해봤자 소용없지만 로그를 위해 남김
            
            return response

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "403" in error_msg or "Quota" in error_msg:
                print(f"📉 [Lock] Key(...{masked_key}) 할당량 초과.", flush=True)
                QUOTA_EXCEEDED_KEYS.add(current_key)
            else:
                # [상세 로그] 왜 에러가 났는지 정확히 출력
                print(f"⚠️ [Error] Key(...{masked_key}) 에러 발생: {error_msg}", flush=True)
            
            time.sleep(0.5)

    print("❌ [Fatal] 모든 키 시도 실패.")
    return None

# ---------------------------------------------------------
# 2. 핵심 함수들
# ---------------------------------------------------------
def standardize_image(image_path, output_path=None):
    try:
        if output_path is None: output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB': img = img.convert('RGB')
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            base, _ = os.path.splitext(output_path)
            new_output_path = f"{base}.jpg"
            img.save(new_output_path, "JPEG", quality=85)
            return new_output_path
    except Exception as e:
        print(f"!! 표준화 실패: {e}", flush=True)
        return image_path

def generate_empty_room(image_path, unique_id, start_time):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    print(f"\n--- [Stage 1] 빈 방 생성 시작 ({MODEL_NAME}) ---", flush=True)
    
    img = Image.open(image_path)
    system_instruction = "You are an expert architectural AI. Your task is to perform structure-preserving image editing."
    
    prompt = (
        "IMAGE EDITING TASK (STRICT):\n"
        "Create a photorealistic image of this room but completely EMPTY.\n"
        "1. REMOVE ALL furniture, rugs, decor, and lighting.\n"
        "2. REMOVE ALL window treatments. Show bare windows/glass.\n"
        "3. KEEP the original floor material, wall color, ceiling structure EXACTLY as they are.\n"
        "4. IN-PAINT the removed areas seamlessly.\n"
        "OUTPUT RULE: Return ONLY the generated image."
    )
    
    # [수정] 안전 설정을 더 강력하게 지정 (모두 허용)
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
    
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }

    max_stage_retries = 3
    for try_count in range(max_stage_retries):
        remaining = max(10, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        
        response = call_gemini_with_failover(
            MODEL_NAME, 
            [prompt, img], 
            request_options={'timeout': remaining},
            safety_settings=safety_settings,
            system_instruction=system_instruction
        )
        
        # [디버깅] 왜 비었는지 확인
        if response:
            if hasattr(response, 'candidates') and response.candidates:
                if hasattr(response, 'parts') and response.parts:
                    # 성공 케이스
                    for part in response.parts:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            print(f">> [성공] 빈 방 이미지 생성됨! (시도 {try_count+1}회차)", flush=True)
                            # ... (저장 로직 생략, 기존 코드와 동일) ...
                            timestamp = int(time.time())
                            filename = f"empty_{timestamp}_{unique_id}.jpg"
                            output_path = os.path.join("outputs", filename)
                            with open(output_path, 'wb') as f: f.write(part.inline_data.data)
                            return standardize_image(output_path)
                else:
                    # 후보는 있는데 파트가 없음 (Safety Filter일 확률 높음)
                    print(f"⚠️ [Blocked] AI가 응답을 생성했지만 안전 필터에 걸렸습니다. (Finish Reason: {response.candidates[0].finish_reason})", flush=True)
            else:
                # 후보조차 없음 (입구컷)
                print(f"⚠️ [Blocked] 응답 후보(Candidates)가 비어있습니다. (Prompt Feedback 확인 필요)", flush=True)

        print(f"⚠️ [Stage 1 실패] 시도 {try_count+1} 실패. 재시도...", flush=True)

    print(">> [최종 실패] 3번 시도했으나 빈 방 생성 불가.", flush=True)
    return image_path

def generate_furnished_room(room_path, style_config, reference_image_path, unique_id, start_time=0):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return None 
    
    try:
        room_img = Image.open(room_path)
        system_instruction = "You are an expert interior designer AI. Your task is virtual staging. You must place furniture realistically."

        prompt = (
            "IMAGE GENERATION TASK (Virtual Staging):\n"
            "Furnish the empty room using the furniture styles shown in the Moodboard.\n"
            "1. RE-ARRANGE: Do NOT copy the layout. Place furniture realistically.\n"
            "2. LIGHTING: Turn on all lights (4000K warm white).\n"
            "3. SCALE & PERSPECTIVE: Match the room's perspective perfectly.\n"
            "OUTPUT RULE: Return ONLY the generated interior image."
        )
        
        input_content = [prompt, "Background Empty Room:", room_img]
        if reference_image_path:
            try:
                ref_img = Image.open(reference_image_path)
                if ref_img.width > 2048 or ref_img.height > 2048: ref_img.thumbnail((2048, 2048))
                input_content.append("Furniture Reference (Moodboard):")
                input_content.append(ref_img)
            except: pass
        
        remaining = max(30, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        response = call_gemini_with_failover(
            MODEL_NAME, 
            input_content, 
            request_options={'timeout': remaining},
            safety_settings=safety_settings,
            system_instruction=system_instruction
        )
        
        # [수정됨] 여기도 동일하게 안전장치 추가
        if response and hasattr(response, 'candidates') and response.candidates:
            try:
                if hasattr(response, 'parts') and response.parts:
                    for part in response.parts:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            timestamp = int(time.time())
                            filename = f"result_{timestamp}_{unique_id}.jpg"
                            output_path = os.path.join("outputs", filename)
                            with open(output_path, 'wb') as f: f.write(part.inline_data.data)
                            return standardize_image(output_path)
            except ValueError:
                 print(f"   ⚠️ [Blocked] 가구 배치 생성 거부됨 ({unique_id})", flush=True)
        
        print(f"   >> [실패] 가구 배치 생성 실패 ({unique_id}) - 응답 없음", flush=True)
        return None 
    except Exception as e:
        print(f"!! Stage 2 에러: {e}", flush=True)
        return None

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
            "prompt": "high quality, 4k, realistic interior, highly detailed",
            "creativity": 2, "hdr": 4, "resemblance": 4, "fractality": 3, "engine": "automatic"
        }
        headers = { "x-freepik-api-key": MAGNIFIC_API_KEY, "Content-Type": "application/json", "Accept": "application/json" }
        response = requests.post(MAGNIFIC_ENDPOINT, json=payload, headers=headers)
        if response.status_code != 200: return image_path
        result_json = response.json()
        if "data" in result_json and "generated" in result_json["data"]:
             return download_image(result_json["data"]["generated"][0], unique_id) or image_path
        elif "data" in result_json and "task_id" in result_json["data"]:
            task_id = result_json["data"]["task_id"]
            while time.time() - start_time < TOTAL_TIMEOUT_LIMIT:
                time.sleep(2)
                status_res = requests.get(f"{MAGNIFIC_ENDPOINT}/{task_id}", headers=headers)
                if status_res.status_code == 200:
                    s_data = status_res.json()
                    if s_data.get("data", {}).get("status") == "COMPLETED":
                        return download_image(s_data["data"]["generated"][0], unique_id) or image_path
                    elif s_data.get("data", {}).get("status") == "FAILED": return image_path
            return image_path
        else: return image_path
    except: return image_path

def download_image(url, unique_id):
    try:
        img_response = requests.get(url)
        if img_response.status_code == 200:
            timestamp = int(time.time())
            filename = f"magnific_{timestamp}_{unique_id}.jpg"
            path = os.path.join("outputs", filename)
            with open(path, "wb") as f: f.write(img_response.content)
            return standardize_image(path)
        return None
    except: return None

# ---------------------------------------------------------
# 3. 라우트 및 엔드포인트
# ---------------------------------------------------------
@app.get("/")
async def read_index(): return FileResponse("static/index.html")

@app.get("/room-types")
async def get_room_types(): return JSONResponse(content=list(ROOM_STYLES.keys()))

@app.get("/styles/{room_type}")
async def get_styles_for_room(room_type: str):
    if room_type in ROOM_STYLES: return JSONResponse(content=ROOM_STYLES[room_type])
    return JSONResponse(content=[], status_code=404)

@app.post("/render")
def render_room(file: UploadFile = File(...), room: str = Form(...), style: str = Form(...), variant: str = Form(...)):
    # [안전장치] 서버 크래시 방지용 try-except
    try:
        full_style = f"{room}-{style}-{variant}"
        unique_id = uuid.uuid4().hex[:8]
        print(f"\n=== 요청 시작 [{unique_id}]: {full_style} (Parallel) ===", flush=True)
        start_time = time.time()
        
        timestamp = int(time.time())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
        raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{safe_name}")
        with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
        std_path = standardize_image(raw_path)
        
        # 1. 빈 방 생성
        step1_img = generate_empty_room(std_path, unique_id, start_time)
        
        # 2. 무드보드 찾기
        ref_path = None
        safe_room = room.lower().replace(" ", "")
        safe_style = style.lower().replace(" ", "-").replace("_", "-")
        target_dir = os.path.join("assets", safe_room, safe_style)
        if os.path.exists(target_dir):
            files = sorted(os.listdir(target_dir))
            for f in files:
                if variant in f: ref_path = os.path.join(target_dir, f); break
            if not ref_path and files: ref_path = os.path.join(target_dir, files[0])

        # 3. 병렬 처리
        generated_results = []
        print(f"\n🚀 [Parallel] 3장 동시 생성 시작!", flush=True)

        def process_one_variant(index):
            sub_id = f"{unique_id}_v{index+1}"
            print(f"   ▶ [Variation {index+1}] 스타트!", flush=True)
            try:
                # 스타일 프롬프트 안전하게 가져오기
                selected_style_prompt = STYLES.get(style, STYLES.get("Modern", "Modern Style"))
                
                result_path = generate_furnished_room(step1_img, selected_style_prompt, ref_path, sub_id, start_time)
                if result_path:
                    print(f"   ✅ [Variation {index+1}] 성공!", flush=True)
                    return f"/outputs/{os.path.basename(result_path)}"
                else:
                    return None
            except Exception as e:
                print(f"   ❌ [Variation {index+1}] 에러: {e}", flush=True)
                return None

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(process_one_variant, i) for i in range(3)]
            for future in futures:
                try:
                    res = future.result()
                    if res: generated_results.append(res)
                except Exception as e:
                    print(f"⚠️ [Thread Error] {e}")
                gc.collect()

        elapsed = time.time() - start_time
        print(f"=== [{unique_id}] 완료. 생성된 이미지: {len(generated_results)}장, 소요시간: {elapsed:.1f}초 ===", flush=True)
        
        if not generated_results: generated_results.append(f"/outputs/{os.path.basename(step1_img)}")

        return JSONResponse(content={
            "original_url": f"/outputs/{os.path.basename(std_path)}", 
            "empty_room_url": f"/outputs/{os.path.basename(step1_img)}", 
            "result_url": generated_results[0], 
            "result_urls": generated_results, 
            "message": "Complete"
        })
    except Exception as e:
        print(f"\n🔥🔥🔥 [SERVER CRASH] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpscaleRequest(BaseModel):
    image_url: str

@app.post("/upscale")
def upscale_and_download(req: UpscaleRequest):
    try:
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "File not found"}, status_code=404)
        
        unique_id = uuid.uuid4().hex[:8]
        start_time = time.time()
        print(f"\n--- [Upscale Request] {filename} ---", flush=True)
        
        final_path = call_magnific_api(local_path, unique_id, start_time)
        return JSONResponse(content={
            "upscaled_url": f"/outputs/{os.path.basename(final_path)}",
            "message": "Success"
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, timeout_keep_alive=300)
