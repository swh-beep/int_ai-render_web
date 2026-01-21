# -*- coding: utf-8 -*-
import os
import asyncio
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
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv
from styles_config import STYLES, ROOM_STYLES
from PIL import Image, ImageOps, ImageDraw
import re
import traceback
import random
import sys
import logging
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic import BaseModel
import gc
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from typing import Optional, List, Dict, Any
from contextvars import ContextVar

# ---------------------------------------------------------
# 1. 환경 설정 및 초기화
# ---------------------------------------------------------
load_dotenv()
LOG_BRIEF = os.getenv("LOG_BRIEF", "1") == "1"
LOG_SUMMARY = os.getenv("LOG_SUMMARY", "1") == "1"
SCALE_CHECK = os.getenv("SCALE_CHECK", "0") == "1"
SUMMARY_REF = ContextVar("SUMMARY_REF", default=None)
GEMINI_MAX_CONCURRENCY_ANALYSIS = int(os.getenv("GEMINI_MAX_CONCURRENCY_ANALYSIS", "30"))
GEMINI_MAX_CONCURRENCY_GEN = int(os.getenv("GEMINI_MAX_CONCURRENCY_GEN", "5"))
GEMINI_SEMAPHORE_ANALYSIS = threading.BoundedSemaphore(max(1, GEMINI_MAX_CONCURRENCY_ANALYSIS))
GEMINI_SEMAPHORE_GEN = threading.BoundedSemaphore(max(1, GEMINI_MAX_CONCURRENCY_GEN))

def _get_gemini_semaphore(model_name: str):
    name = (model_name or "").lower()
    analysis_name = (ANALYSIS_MODEL_NAME or "").lower()
    if name == analysis_name or "flash" in name:
        return GEMINI_SEMAPHORE_ANALYSIS
    return GEMINI_SEMAPHORE_GEN

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

print(f"[Env] API key count: {len(API_KEY_POOL)}", flush=True)

MAGNIFIC_API_KEY = os.getenv("MAGNIFIC_API_KEY")
MAGNIFIC_ENDPOINT = os.getenv("MAGNIFIC_ENDPOINT", "https://api.freepik.com/v1/ai/image-upscaler")
TOTAL_TIMEOUT_LIMIT = 300 

os.makedirs("outputs", exist_ok=True)
os.makedirs("assets", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Periodic cleanup for outputs to avoid disk growth.
OUTPUT_CLEANUP_TTL_SEC = 12 * 60 * 60
OUTPUT_CLEANUP_INTERVAL_SEC = 60 * 60

def _cleanup_outputs_once():
    now = time.time()
    try:
        for name in os.listdir("outputs"):
            path = os.path.join("outputs", name)
            try:
                if not os.path.isfile(path):
                    continue
                if now - os.path.getmtime(path) > OUTPUT_CLEANUP_TTL_SEC:
                    os.remove(path)
            except Exception:
                pass
    except Exception:
        pass

def _start_outputs_cleanup_worker():
    def _worker():
        while True:
            _cleanup_outputs_once()
            time.sleep(OUTPUT_CLEANUP_INTERVAL_SEC)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()

_start_outputs_cleanup_worker()

app = FastAPI()

def async_wrap(func):
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await run_in_threadpool(func, *args, **kwargs)
    return wrapper
@app.middleware("http")
async def log_requests(request, call_next):
    rid = uuid.uuid4().hex[:8]
    t0 = time.time()
    if not LOG_BRIEF:
        logger.info(f"[REQ {rid}] {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        dt = (time.time() - t0) * 1000
        if not LOG_BRIEF:
            logger.info(f"[RES {rid}] {response.status_code} ({dt:.1f}ms) {request.url.path}")
        return response
    except Exception as e:
        dt = (time.time() - t0) * 1000
        logger.exception(f"[ERR {rid}] ({dt:.1f}ms) {request.url.path} :: {e}")
        raise

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
    timeout_retry_used = False

    # 간단하게 payload 타입만 로깅 (이미지는 너무 크니 길이만)
    try:
        content_types = []
        for c in contents or []:
            if isinstance(c, str):
                content_types.append(f"str({len(c)})")
            else:
                content_types.append(type(c).__name__)
        if not LOG_BRIEF:

            logger.info(f"[Gemini] model={model_name} timeout={request_options.get('timeout')} contents={content_types}")
    except Exception:
        pass

    for attempt in range(max_retries):
        available_keys = [k for k in API_KEY_POOL if k not in QUOTA_EXCEEDED_KEYS]
        if not available_keys:
            logger.warning("[Gemini] All keys locked. Cooldown 5s then reset.")
            time.sleep(5)
            QUOTA_EXCEEDED_KEYS.clear()
            available_keys = list(API_KEY_POOL)

        current_key = random.choice(available_keys)
        masked_key = current_key[-4:]

        try:
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel(model_name, system_instruction=system_instruction) if system_instruction else genai.GenerativeModel(model_name)

            with _get_gemini_semaphore(model_name):
                t0 = time.time()
                response = model.generate_content(contents, request_options=request_options, safety_settings=safety_settings)
                dt = (time.time() - t0) * 1000
            if not LOG_BRIEF:
                logger.info(f"[Gemini] success key=...{masked_key} ({dt:.0f}ms) model={model_name}")
            return response

        except Exception as e:
            error_msg = str(e)
            error_lower = error_msg.lower()
            is_timeout = any(x in error_lower for x in ["504", "deadline", "timeout", "timed out"])
            if is_timeout:
                if timeout_retry_used:
                    logger.error(f"[Gemini] timeout retry exhausted key=...{masked_key} attempt={attempt+1}/{max_retries} :: {error_msg[:200]}")
                    break
                timeout_retry_used = True
                logger.warning(f"[Gemini] timeout key=...{masked_key} attempt={attempt+1}/{max_retries} :: {error_msg[:200]}")
                time.sleep(1)
                continue
            if any(x in error_msg for x in ["429", "403", "Quota", "limit", "Resource has been exhausted"]):
                logger.warning(f"[Gemini] 📉 quota key=...{masked_key} attempt={attempt+1}/{max_retries} :: {error_msg[:180]}")
                QUOTA_EXCEEDED_KEYS.add(current_key)
                time.sleep(2 + attempt)
            else:
                logger.error(f"[Gemini] ⚠️ error key=...{masked_key} attempt={attempt+1}/{max_retries} :: {error_msg[:250]}")
                time.sleep(1)

    logger.error("[Gemini] ❌ fatal: all keys failed")
    return None

# ---------------------------------------------------------
# [LOGGING] Always-on stdout logging (works under uvicorn/gunicorn)
# ---------------------------------------------------------
def setup_logging():
    try:
        # Make stdout line-buffered so logs appear immediately
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=True)
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,  # <-- 중요: uvicorn이 이미 로깅 잡았어도 덮어씀
    )

setup_logging()
logger = logging.getLogger("app")
if LOG_BRIEF:
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
LOG_SECTION = '=' * 72
LOG_STEP = '-' * 72

def log_section(title: str):
    logger.info(LOG_SECTION)
    logger.info(title)
    logger.info(LOG_SECTION)

def log_step(title: str):
    logger.info(LOG_STEP)
    logger.info(title)

logger.info("[Logger] initialized (stdout, line-buffered).")

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


def standardize_image_to_target_canvas(
    image_path: str,
    target_path: str,
    output_path: Optional[str] = None,
) -> str:
    """Force output to match the original target image size, ignoring references."""
    try:
        with Image.open(target_path) as tgt_img:
            tgt_img = ImageOps.exif_transpose(tgt_img)
            tgt_w, tgt_h = tgt_img.size
            if tgt_w <= 0 or tgt_h <= 0:
                return image_path

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            w, h = img.size
            if w <= 0 or h <= 0:
                return image_path

            target_ratio = tgt_w / tgt_h
            current_ratio = w / h

            # If aspect already matches, just resize to exact target size.
            if abs(current_ratio - target_ratio) < 1e-3:
                resized = img.resize((tgt_w, tgt_h), Image.Resampling.LANCZOS)
            else:
                # Center-crop to target ratio, then resize to exact target size.
                if current_ratio > target_ratio:
                    new_w = int(h * target_ratio)
                    x0 = max(0, (w - new_w) // 2)
                    img = img.crop((x0, 0, x0 + new_w, h))
                else:
                    new_h = int(w / target_ratio)
                    y0 = max(0, (h - new_h) // 2)
                    img = img.crop((0, y0, w, y0 + new_h))
                resized = img.resize((tgt_w, tgt_h), Image.Resampling.LANCZOS)

            base, _ = os.path.splitext(output_path or image_path)
            out_path = f"{base}_target.png"
            resized.save(out_path, "PNG")
            return out_path
    except Exception as e:
        print(f"!! [Target Canvas Fit Failed] {e}", flush=True)
        return image_path
# -----------------------------------------------------------------------------
# [CORE] Analysis Logic (Global Definition)
# -----------------------------------------------------------------------------

# =============================================================================
# [SCALE FIX PACK vB] Robust dimension parsing + furniture spec JSON + auto-pick
# - Keeps existing rendering behavior; only strengthens SCALE guidance & selection.
# - Primary anchor furniture = largest-volume movable furniture EXCLUDING rugs/carpets.
# =============================================================================

_RUG_KEYWORDS = [
    "fabric rug", "large rug", "rug", "carpet", "mat",
    "러그", "카페트", "카펫",
]

def _is_rug_like(label: str) -> bool:
    try:
        s = (label or "").strip().lower()
        if not s:
            return False
        for kw in _RUG_KEYWORDS:
            if kw in s:
                if kw == "mat":
                    if re.search(r"\bmat\b", s):
                        return True
                    continue
                return True
        return False
    except Exception:
        return False

def _to_mm(value: float, unit: Optional[str]) -> int:
    """Convert value to mm. If unit missing, heuristic: >=50 -> mm else meters."""
    u = (unit or "").strip().lower()
    try:
        if u in ("mm",):
            return int(round(value))
        if u in ("cm",):
            return int(round(value * 10.0))
        if u in ("m", "meter", "metre"):
            return int(round(value * 1000.0))
        if value <= 20.0:
            return int(round(value * 1000.0))
        return int(round(value))
    except Exception:
        return 0

_DIM_KEY_PATTERNS = {
    "width_mm":  r"(?:\bW\b|width|가로|폭|너비)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
    "depth_mm":  r"(?:\bD\b|depth|세로|깊이)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
    "height_mm": r"(?:\bH\b|height|높이)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
}
_LENGTH_PAT = r"(?:\bL\b|length|len)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?"

_TRIPLE_PATTERNS = [
    r"([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*[x×X]\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*[x×X]\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
    r"\bW\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*\bD\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*\bH\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
]
_DOUBLE_PATTERNS = [
    r"([0-9][0-9,\.]*)\s*(mm|cm|m)?\s*[x×X]\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?",
]

def parse_object_dimensions_mm(text: str) -> dict:
    t = (text or "")
    t_norm = t.replace("，", ",").replace("×", "x")
    out = {"width_mm": None, "depth_mm": None, "height_mm": None, "raw": {}}

    for pat in _TRIPLE_PATTERNS:
        m = re.search(pat, t_norm, flags=re.IGNORECASE)
        if not m:
            continue
        n1, u1, n2, u2, n3, u3 = m.groups()
        def _num(s): return float(str(s).replace(",", ""))
        w = _to_mm(_num(n1), u1)
        d = _to_mm(_num(n2), u2 or u1)
        h = _to_mm(_num(n3), u3 or u2 or u1)
        if w: out["width_mm"] = w
        if d: out["depth_mm"] = d
        if h: out["height_mm"] = h
        out["raw"]["triple"] = m.group(0)
        return out

    for k, pat in _DIM_KEY_PATTERNS.items():
        m = re.search(pat, t_norm, flags=re.IGNORECASE)
        if not m:
            continue
        num_str, unit = m.group(1), m.group(2)
        try:
            v = float(num_str.replace(",", ""))
        except Exception:
            continue
        mm = _to_mm(v, unit)
        if mm:
            out[k] = mm
            out["raw"][k] = m.group(0)

    if not out["width_mm"]:
        m = re.search(_LENGTH_PAT, t_norm, flags=re.IGNORECASE)
        if m:
            num_str, unit = m.group(1), m.group(2)
            try:
                v = float(num_str.replace(",", ""))
            except Exception:
                v = None
            if v is not None:
                mm = _to_mm(v, unit)
                if mm:
                    out["width_mm"] = mm
                    out["raw"]["length"] = m.group(0)

    if not out["height_mm"]:
        m = re.search(r"(?:\bSH\b|SH)\s*[:=]?\s*([0-9][0-9,\.]*)\s*(mm|cm|m)?", t_norm, flags=re.IGNORECASE)
        if m:
            num_str, unit = m.group(1), m.group(2)
            try:
                v = float(num_str.replace(",", ""))
            except Exception:
                v = None
            if v is not None:
                mm = _to_mm(v, unit)
                if mm:
                    out["height_mm"] = mm
                    out["raw"]["seat_height"] = m.group(0)

    if not any([out["width_mm"], out["depth_mm"], out["height_mm"]]):
        for pat in _DOUBLE_PATTERNS:
            m = re.search(pat, t_norm, flags=re.IGNORECASE)
            if not m:
                continue
            n1, u1, n2, u2 = m.groups()
            def _num(s): return float(str(s).replace(",", ""))
            v1 = _to_mm(_num(n1), u1)
            v2 = _to_mm(_num(n2), u2 or u1)
            if re.search(r"\b(poster|frame|wall|art|painting)\b", t_norm, flags=re.IGNORECASE):
                if v1: out["width_mm"] = v1
                if v2: out["height_mm"] = v2
            else:
                if v1: out["width_mm"] = v1
                if v2: out["depth_mm"] = v2
            out["raw"]["double"] = m.group(0)
            break

    return out

def parse_room_dimensions_mm(text: str) -> dict:
    t = (text or "").strip()
    if not t:
        return {"width_mm": 0, "depth_mm": 0, "height_mm": 0}
    t_norm = t.replace("，", ",").replace("×", "x").replace("X", "x")

    m = re.search(_TRIPLE_PATTERNS[0], t_norm, flags=re.IGNORECASE)
    if m:
        n1,u1,n2,u2,n3,u3 = m.groups()
        def _num(s): return float(str(s).replace(",", ""))
        w = _to_mm(_num(n1), u1)
        d = _to_mm(_num(n2), u2 or u1)
        h = _to_mm(_num(n3), u3 or u2 or u1)
        return {"width_mm": w or 0, "depth_mm": d or 0, "height_mm": h or 0}

    parts = re.findall(r"([0-9][0-9,\.]*)\s*(mm|cm|m)?", t_norm, flags=re.IGNORECASE)
    nums = []
    for num_str, unit in parts:
        try:
            v = float(num_str.replace(",", ""))
        except Exception:
            continue
        nums.append(_to_mm(v, unit))
    nums = [n for n in nums if n > 0]
    if not nums:
        return {"width_mm": 0, "depth_mm": 0, "height_mm": 0}
    if len(nums) == 1:
        return {"width_mm": nums[0], "depth_mm": 0, "height_mm": 0}
    if len(nums) == 2:
        return {"width_mm": nums[0], "depth_mm": nums[1], "height_mm": 0}
    return {"width_mm": nums[0], "depth_mm": nums[1], "height_mm": nums[2]}

def _volume_proxy(dims: dict) -> int:
    try:
        w = int(dims.get("width_mm") or 0)
        d = int(dims.get("depth_mm") or 0)
        h = int(dims.get("height_mm") or 0)
        if w and d and h: return w*d*h
        if w and d: return w*d
        if w: return w
    except Exception:
        pass
    return 0

def build_furniture_specs_json(analyzed_items: list) -> dict:
    items = []
    
    # [FIX] 우선순위 카테고리 정의 (이 단어가 포함되면 가중치 부여)
    PRIORITY_KEYWORDS = {
        "sofa": 100, "couch": 100, "sectional": 100,
        "bed": 90, 
        "table": 80, "desk": 80, "dining": 80,
        "console": 60, "shelf": 60, "cabinet": 60, "storage": 60,
        "tv": 50,
        "chair": 40, "armchair": 40,
        "lamp": 10, "light": 10, "plant": 5
    }

    for i, it in enumerate(analyzed_items or []):
        label = it.get("label", "") or ""
        desc  = it.get("description", "") or ""
        box   = it.get("box_2d")
        
        # 1. 치수 파싱 시도 (분석된 description에서)
        dims = parse_object_dimensions_mm(desc)
        
        # 2. 러그 판단
        is_rug = _is_rug_like(label)
        
        # 3. 부피 대용값 계산 (높이 없으면 기본값 1000mm 가정하여 0 방지)
        w = dims.get("width_mm") or 0
        d = dims.get("depth_mm") or 0
        h = dims.get("height_mm") or 1000 
        vp = (w * d * h) if (w or d) else 0
        if is_rug: vp = 0 # 러그는 기준점 제외

        # 4. 카테고리 점수 계산
        cat_score = 0
        norm_label = label.lower()
        for key, score in PRIORITY_KEYWORDS.items():
            if key in norm_label:
                cat_score = max(cat_score, score)
        
        items.append({
            "index": i,
            "label": label,
            "is_rug": is_rug,
            "category_score": cat_score, # [NEW]
            "dims_mm": {
                "width_mm": dims.get("width_mm"),
                "depth_mm": dims.get("depth_mm"),
                "height_mm": dims.get("height_mm"),
            },
            "volume_proxy": vp,
            "box_2d": box,
            "description": desc,
            "crop_path": (it.get("crop_path") if isinstance(it, dict) else None),
        })

    primary = None
    # [FIX] 기준점 선정 로직 강화: (카테고리 점수 > 부피 > 인덱스)
    candidates = [x for x in items if not x["is_rug"]]
    if candidates:
        # Sort key: 1. Category Score (desc), 2. Volume (desc), 3. Index (asc)
        candidates_sorted = sorted(candidates, key=lambda x: (x["category_score"], x["volume_proxy"], -x["index"]), reverse=True)
        primary = candidates_sorted[0]

    # Max width fallback
    max_width_mm = 0
    for x in candidates:
        w = x.get("dims_mm", {}).get("width_mm") or 0
        try:
            max_width_mm = max(max_width_mm, int(w))
        except Exception: pass

    try:
        if primary and max_width_mm and not (primary.get("dims_mm", {}) or {}).get("width_mm"):
            primary.setdefault("dims_mm", {})["width_mm"] = int(max_width_mm)
    except Exception: pass

    hierarchy = [x.get("label","") for x in (analyzed_items or []) if not _is_rug_like(x.get("label",""))]
    return {"items": items, "primary": primary, "max_width_mm": max_width_mm, "size_hierarchy": hierarchy}

def _safe_json_from_model_text(txt: str):
    if not txt: return None
    t = txt.strip()
    try:
        if "```json" in t:
            t = t.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in t:
            t = t.split("```", 1)[1].split("```", 1)[0].strip()
    except Exception:
        pass
    try:
        return json.loads(t)
    except Exception:
        pass
    try:
        a = t.find("{"); b = t.rfind("}")
        if a != -1 and b != -1 and b > a:
            return json.loads(t[a:b+1])
    except Exception:
        pass
    return None

def detect_room_planes_norm(empty_room_path: str):
    try:
        with Image.open(empty_room_path) as img:
            prompt = (
                "TASK: ROOM GEOMETRY MEASUREMENT.\n"
                "Locate the BACK WALL rectangle and FLOOR plane cues in this empty room image.\n"
                "Return STRICT JSON ONLY:\n"
                "{\"x_left\":0.0,\"x_right\":1.0,\"y_top\":0.0,\"y_bottom\":1.0,"
                "\"floor_front_y\":1.0,\"vanish_x\":0.5,\"vanish_y\":0.5}\n"
                "Definitions:\n"
                "- x_left/x_right/y_top/y_bottom: back wall rectangle bounds.\n"
                "- floor_front_y: closest visible floor edge (frontmost).\n"
                "- vanish_x/vanish_y: floor vanishing point where floor lines converge.\n"
                "All values normalized 0..1."
            )
            res = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [prompt, img], {"timeout": 25}, {})
            obj = _safe_json_from_model_text(res.text if res and hasattr(res, "text") else "")
            if isinstance(obj, dict):
                def _clamp(v):
                    try:
                        return max(0.0, min(1.0, float(v)))
                    except Exception:
                        return None

                xl = _clamp(obj.get("x_left"))
                xr = _clamp(obj.get("x_right"))
                yt = _clamp(obj.get("y_top"))
                yb = _clamp(obj.get("y_bottom"))
                ff = _clamp(obj.get("floor_front_y"))
                vx = _clamp(obj.get("vanish_x"))
                vy = _clamp(obj.get("vanish_y"))

                if None in (xl, xr, yt, yb):
                    return None
                if xr - xl < 0.2 or yb - yt < 0.2:
                    return None

                if ff is None:
                    ff = 1.0
                if ff < yb:
                    ff = yb
                if ff <= yb + 0.01:
                    ff = min(1.0, yb + 0.05)

                if vx is None:
                    vx = (xl + xr) / 2.0
                if vy is None:
                    vy = yt

                return {
                    "x_left": xl,
                    "x_right": xr,
                    "y_top": yt,
                    "y_bottom": yb,
                    "floor_front_y": ff,
                    "vanish_x": vx,
                    "vanish_y": vy,
                }
    except Exception:
        pass
    return None

def detect_back_wall_span_norm(empty_room_path: str) -> tuple:
    try:
        with Image.open(empty_room_path) as img:
            prompt = (
                "TASK: ROOM GEOMETRY MEASUREMENT.\\n"
                "In this empty room photo, find the BACK WALL usable span where main furniture would sit.\\n"
                "Return STRICT JSON ONLY: {\\\"x_left\\\":0.0, \\\"x_right\\\":1.0} using normalized [0..1].\\n"
                "Use the floor-wall boundary; ignore doors/windows if they reduce usable span. Approximate if unsure."
            )
            res = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [prompt, img], {"timeout": 20}, {})
            obj = _safe_json_from_model_text(res.text if res and hasattr(res, "text") else "")
            if isinstance(obj, dict):
                xl = float(obj.get("x_left", 0.0)); xr = float(obj.get("x_right", 1.0))
                xl = max(0.0, min(1.0, xl)); xr = max(0.0, min(1.0, xr))
                if xr - xl >= 0.2:
                    return (xl, xr)
    except Exception:
        pass
    return (0.0, 1.0)

def _crop_ref_item_image(ref_path: str, box_2d: list, out_path: str):
    try:
        if not box_2d:
            return None
        with Image.open(ref_path) as img:
            w, h = img.size
            ymin, xmin, ymax, xmax = box_2d
            left = int(xmin / 1000 * w); top = int(ymin / 1000 * h)
            right = int(xmax / 1000 * w); bottom = int(ymax / 1000 * h)
            left = max(0, min(w-1, left)); right = max(left+1, min(w, right))
            top = max(0, min(h-1, top)); bottom = max(top+1, min(h, bottom))
            crop = img.crop((left, top, right, bottom))
            crop.save(out_path, "PNG")
            return out_path
    except Exception:
        return None

def create_scale_guide_image(
    empty_room_path: str,
    wall_span_norm: tuple,
    target_ratio: float,
    out_path: str,
    room_planes: dict = None,
    target_ratios: dict = None,
):
    try:
        with Image.open(empty_room_path) as base_img:
            base = base_img.convert("RGBA")
            W, H = base.size
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            xl, xr = wall_span_norm if wall_span_norm else (0.0, 1.0)
            xl = max(0.0, min(1.0, float(xl)))
            xr = max(0.0, min(1.0, float(xr)))

            if room_planes and target_ratios and all(k in target_ratios for k in ("w", "d", "h")):
                yt = float(room_planes.get("y_top", 0.0))
                yb = float(room_planes.get("y_bottom", 1.0))
                ff = float(room_planes.get("floor_front_y", 1.0))
                vx = float(room_planes.get("vanish_x", (xl + xr) / 2.0))
                vy = float(room_planes.get("vanish_y", yt))

                yt = max(0.0, min(1.0, yt))
                yb = max(0.0, min(1.0, yb))
                ff = max(0.0, min(1.0, ff))
                vx = max(0.0, min(1.0, vx))
                vy = max(0.0, min(1.0, vy))
                if ff < yb:
                    ff = yb

                span_px = max(1, int((xr - xl) * W))
                wall_h_px = max(1, int((yb - yt) * H))

                w_ratio = max(0.01, min(1.0, float(target_ratios.get("w") or 0.0)))
                d_ratio = max(0.01, min(1.0, float(target_ratios.get("d") or 0.0)))
                h_ratio = max(0.01, min(1.0, float(target_ratios.get("h") or 0.0)))

                target_w_px = int(span_px * w_ratio)
                target_h_px = int(wall_h_px * h_ratio)

                x_center = int((xl + xr) * 0.5 * W)
                x1 = int(max(int(xl * W), min(int(xr * W), x_center - target_w_px // 2)))
                x2 = int(max(int(xl * W), min(int(xr * W), x_center + target_w_px // 2)))

                # W guide (red)
                line_w = (255, 0, 0, 110)
                thick = 4
                for dx in range(-thick // 2, thick // 2 + 1):
                    draw.line([(x1 + dx, int(yt * H)), (x1 + dx, int(yb * H))], fill=line_w, width=1)
                    draw.line([(x2 + dx, int(yt * H)), (x2 + dx, int(yb * H))], fill=line_w, width=1)

                # H guide (green)
                h_line = (0, 255, 0, 110)
                y_top_item = int(max(int(yt * H), int(yb * H) - target_h_px))
                draw.line([(int(xl * W), y_top_item), (int(xr * W), y_top_item)], fill=h_line, width=3)

                # D guide (blue) along floor center line
                back_y = int(yb * H)
                front_y = int(ff * H)
                vanish_x = int(vx * W)
                vanish_y = int(vy * H)
                front_x = int(W * 0.5)

                denom = (front_y - vanish_y)
                if denom != 0:
                    t_back = (back_y - vanish_y) / denom
                else:
                    t_back = None
                if t_back is not None and 0.0 <= t_back <= 1.0:
                    back_x = int(vanish_x + t_back * (front_x - vanish_x))
                else:
                    back_x = x_center

                back_pt = (back_x, back_y)
                front_pt = (front_x, front_y)
                depth_x = int(back_x + d_ratio * (front_x - back_x))
                depth_y = int(back_y + d_ratio * (front_y - back_y))
                depth_pt = (depth_x, depth_y)

                line_d_full = (0, 128, 255, 60)
                line_d = (0, 128, 255, 160)
                draw.line([back_pt, front_pt], fill=line_d_full, width=2)
                draw.line([back_pt, depth_pt], fill=line_d, width=4)

                # Wall span line (cyan)
                wall_line = (0, 255, 255, 80)
                draw.line([(int(xl * W), back_y), (int(xr * W), back_y)], fill=wall_line, width=3)
            else:
                span_px = max(1, int((xr - xl) * W))
                target_px = int(max(1, min(span_px, span_px * float(target_ratio))))
                x_center = int((xl + xr) * 0.5 * W)
                x1 = int(max(0, min(W - 1, x_center - target_px // 2)))
                x2 = int(max(0, min(W - 1, x_center + target_px // 2)))

                line = (255, 0, 0, 110)
                thick = 4
                for dx in range(-thick // 2, thick // 2 + 1):
                    draw.line([(x1 + dx, 0), (x1 + dx, H)], fill=line, width=1)
                    draw.line([(x2 + dx, 0), (x2 + dx, H)], fill=line, width=1)

                wall_line = (0, 255, 255, 80)
                draw.line([(int(xl * W), H - 5), (int(xr * W), H - 5)], fill=wall_line, width=3)

            composed = Image.alpha_composite(base, overlay).convert("RGB")
            composed.save(out_path, "PNG")
            return out_path
    except Exception:
        return None

def detect_primary_bbox_norm(staged_path: str, ref_item_crop_path: Optional[str], primary_label: Optional[str]):
    try:
        with Image.open(staged_path) as img:
            prompt = (
                "OBJECT LOCALIZATION TASK.\\n"
                "Find the PRIMARY ANCHOR furniture in the staged room image.\\n"
                "Return STRICT JSON ONLY: {\\\"xmin\\\":0.0,\\\"ymin\\\":0.0,\\\"xmax\\\":1.0,\\\"ymax\\\":1.0}.\\n"
                "bbox must tightly cover only that furniture. If reference crop is provided, match that object."
            )
            content = [prompt, "Staged room image:", img]
            if primary_label:
                content.insert(1, f"Primary label hint: {primary_label}")
            ref_img = None
            try:
                if ref_item_crop_path and os.path.exists(ref_item_crop_path):
                    ref_img = Image.open(ref_item_crop_path)
                    content += ["Reference item crop:", ref_img]
                res = call_gemini_with_failover(ANALYSIS_MODEL_NAME, content, {"timeout": 20}, {})
            finally:
                if ref_img:
                    ref_img.close()
            obj = _safe_json_from_model_text(res.text if res and hasattr(res, "text") else "")
            if isinstance(obj, dict):
                xmin = float(obj.get("xmin", 0.0)); xmax = float(obj.get("xmax", 1.0))
                ymin = float(obj.get("ymin", 0.0)); ymax = float(obj.get("ymax", 1.0))
                xmin = max(0.0, min(1.0, xmin)); xmax = max(0.0, min(1.0, xmax))
                ymin = max(0.0, min(1.0, ymin)); ymax = max(0.0, min(1.0, ymax))
                if xmax - xmin > 0.05 and ymax - ymin > 0.05:
                    return (xmin, ymin, xmax, ymax)
    except Exception:
        pass
    return None

def detect_item_bbox_norm(staged_path: str, ref_item_crop_path: Optional[str], item_label: Optional[str]):
    try:
        with Image.open(staged_path) as img:
            prompt = (
                "OBJECT LOCALIZATION TASK.\n"
                "Find the specified furniture item in the staged room image.\n"
                "Return STRICT JSON ONLY: {\"xmin\":0.0,\"ymin\":0.0,\"xmax\":1.0,\"ymax\":1.0}.\n"
                "bbox must tightly cover only that furniture. If reference crop is provided, match that object."
            )
            content = [prompt, "Staged room image:", img]
            if item_label:
                content.insert(1, f"Item label hint: {item_label}")
            ref_img = None
            try:
                if ref_item_crop_path and os.path.exists(ref_item_crop_path):
                    ref_img = Image.open(ref_item_crop_path)
                    content += ["Reference item crop:", ref_img]
                res = call_gemini_with_failover(ANALYSIS_MODEL_NAME, content, {"timeout": 20}, {})
            finally:
                if ref_img:
                    ref_img.close()
            obj = _safe_json_from_model_text(res.text if res and hasattr(res, "text") else "")
            if isinstance(obj, dict):
                xmin = float(obj.get("xmin", 0.0)); xmax = float(obj.get("xmax", 1.0))
                ymin = float(obj.get("ymin", 0.0)); ymax = float(obj.get("ymax", 1.0))
                xmin = max(0.0, min(1.0, xmin)); xmax = max(0.0, min(1.0, xmax))
                ymin = max(0.0, min(1.0, ymin)); ymax = max(0.0, min(1.0, ymax))
                if xmax - xmin > 0.05 and ymax - ymin > 0.05:
                    return (xmin, ymin, xmax, ymax)
    except Exception:
        pass
    return None

def _score_scale(bbox_norm: tuple, wall_span_norm: tuple, target_ratio: float) -> float:
    try:
        xmin, ymin, xmax, ymax = bbox_norm
        xl, xr = wall_span_norm if wall_span_norm else (0.0, 1.0)
        span = max(1e-6, (xr - xl))
        w = max(1e-6, (xmax - xmin))
        actual = w / span
        target = max(1e-6, float(target_ratio))
        err = abs(actual - target)
        tol = max(0.08, target * 0.20)
        score = 1.0 - min(1.0, err / tol)
        return float(max(0.0, min(1.0, score)))
    except Exception:
        return 0.0

def reorder_by_scale_best_pick(result_urls: list, ref_path: str, primary: dict, room_dims: dict, wall_span_norm: tuple) -> list:
    try:
        room_w = int(room_dims.get("width_mm") or 0)
        p_w = int((primary.get("dims_mm") or {}).get("width_mm") or 0)
        if room_w <= 0 or p_w <= 0:
            return result_urls
        target_ratio = p_w / room_w

        ref_crop = None
        try:
            out_crop = os.path.join("outputs", f"ref_primary_{uuid.uuid4().hex[:8]}.png")
            ref_crop = _crop_ref_item_image(ref_path, primary.get("box_2d"), out_crop) if primary.get("box_2d") else None
        except Exception:
            ref_crop = None

        scored = []
        for idx, url in enumerate(result_urls or []):
            local = os.path.join("outputs", os.path.basename(url))
            bbox = detect_primary_bbox_norm(local, ref_crop, primary.get("label"))
            if bbox is None:
                scored.append((0.0, idx, url))
                continue
            s = _score_scale(bbox, wall_span_norm, target_ratio)
            scored.append((s, idx, url))

        scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        return [u for _, _, u in scored]
    except Exception:
        return result_urls

def validate_furnished_scale(
    staged_path: str,
    furniture_specs_json: dict,
    room_dims: dict,
    room_planes: Optional[dict],
    primary_label: Optional[str] = None,
):
    try:
        if not furniture_specs_json or not isinstance(furniture_specs_json, dict):
            return True, []
        items = furniture_specs_json.get("items") or []
        if not items:
            return True, []

        room_h = int((room_dims or {}).get("height_mm") or 0)
        wall_h_norm = None
        if room_planes:
            try:
                yt = float(room_planes.get("y_top", 0.0))
                yb = float(room_planes.get("y_bottom", 1.0))
                yt = max(0.0, min(1.0, yt))
                yb = max(0.0, min(1.0, yb))
                wall_h_norm = max(1e-6, (yb - yt))
            except Exception:
                wall_h_norm = None

        complete_items = []
        for it in items:
            if it.get("is_rug"):
                continue
            dm = it.get("dims_mm") or {}
            w = int(dm.get("width_mm") or 0)
            d = int(dm.get("depth_mm") or 0)
            h = int(dm.get("height_mm") or 0)
            if w > 0 and d > 0 and h > 0:
                complete_items.append(it)

        if not complete_items:
            return True, []

        if not primary_label:
            primary_label = (furniture_specs_json.get("primary") or {}).get("label")
        if not primary_label:
            primary_label = (complete_items[0].get("label") or "")

        bboxes = {}
        for it in complete_items:
            label = it.get("label") or "Item"
            bbox = detect_item_bbox_norm(staged_path, it.get("crop_path"), label)
            if bbox:
                bboxes[label] = bbox

        primary_bbox = bboxes.get(primary_label)
        primary_dims = None
        for it in complete_items:
            if (it.get("label") or "") == primary_label:
                primary_dims = it.get("dims_mm") or {}
                break

        if not primary_bbox or not primary_dims:
            return True, []

        p_h_mm = float(primary_dims.get("height_mm") or 0)
        if p_h_mm <= 0:
            return True, []

        xmin, ymin, xmax, ymax = primary_bbox
        primary_h_px = max(1e-6, (ymax - ymin))

        tol_rel = 0.10
        tol_room = 0.10
        issues = []

        for it in complete_items:
            label = it.get("label") or "Item"
            if label == primary_label:
                continue
            bbox = bboxes.get(label)
            if not bbox:
                continue
            dm = it.get("dims_mm") or {}
            h_mm = float(dm.get("height_mm") or 0)
            if h_mm <= 0:
                continue

            xmin, ymin, xmax, ymax = bbox
            h_px = max(1e-6, (ymax - ymin))
            obs_rel = h_px / primary_h_px
            exp_rel = h_mm / p_h_mm
            if not LOG_BRIEF:
                logger.info(
                    "[ScaleCheck] %s rel_obs=%.3f rel_exp=%.3f",
                    label,
                    obs_rel,
                    exp_rel,
                )
            rel_thresh = max(1.05, exp_rel * (1.0 + tol_rel))
            if obs_rel > rel_thresh:
                issues.append(f"{label} taller than expected vs primary")

            if room_h > 0 and wall_h_norm:
                obs_room = h_px / wall_h_norm
                exp_room = h_mm / room_h
                if not LOG_BRIEF:
                    logger.info(
                        "[ScaleCheck] %s room_obs=%.3f room_exp=%.3f",
                        label,
                        obs_room,
                        exp_room,
                    )
                room_thresh = max(1.10, exp_room * (1.0 + tol_room))
                if obs_room > room_thresh:
                    issues.append(f"{label} exceeds expected room height ratio")

        if issues:
            return False, issues
        return True, []
    except Exception:
        return True, []

def detect_furniture_boxes(moodboard_path):
    if not LOG_BRIEF:
        print(f">> [Detection] Scanning furniture in {moodboard_path}...", flush=True)
    try:
        with Image.open(moodboard_path) as img:
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
                    if not LOG_BRIEF:
                        print(f">> [Detection] Found {len(items)} items (Sorted): {[i.get('label') for i in items]}", flush=True)
                    return items
    except Exception as e:
        print(f"!! Detection Failed: {e}", flush=True)
    
    return [{"label": "Main Furniture"}, {"label": "Coffee Table"}, {"label": "Lounge Chair"}]

def analyze_cropped_item(moodboard_path, item_data, unique_id=None, item_index=None, save_crop=True):
    """
    Crop detected item WITH PADDING to capture specification text below the item.
    """
    try:
        box = item_data.get('box_2d')
        label = item_data.get('label', 'Furniture')
        cropped_img = None
        cutout_img = None
        
        img = Image.open(moodboard_path)
        W, H = img.size
        
        if box:
            ymin, xmin, ymax, xmax = box

            # Base crop for design reference (exclude spec text).
            base_top = int(ymin / 1000 * H)
            base_bottom = int(ymax / 1000 * H)
            base_left = int(xmin / 1000 * W)
            base_right = int(xmax / 1000 * W)

            box_w_px = max(1, base_right - base_left)
            box_h_px = max(1, base_bottom - base_top)

            # Expand aggressively to capture nearby spec text.
            pad_bottom_px = max(int(box_h_px * 2.0), int(H * 0.18))
            pad_top_px = max(int(box_h_px * 1.2), int(H * 0.12))
            pad_left_px = max(int(box_w_px * 1.2), int(W * 0.16))
            pad_right_px = max(int(box_w_px * 2.0), int(W * 0.24))

            # Bias expansion toward the side with more whitespace.
            space_left = base_left
            space_right = W - base_right
            if space_right > space_left * 1.2:
                pad_right_px = max(pad_right_px, int(W * 0.34))
                pad_left_px = max(pad_left_px, int(W * 0.12))
            elif space_left > space_right * 1.2:
                pad_left_px = max(pad_left_px, int(W * 0.34))
                pad_right_px = max(pad_right_px, int(W * 0.12))

            top = max(0, base_top - pad_top_px)
            bottom = min(H, base_bottom + pad_bottom_px)
            left = max(0, base_left - pad_left_px)
            right = min(W, base_right + pad_right_px)

            # Ensure a minimum crop window for small items.
            min_w = int(W * 0.26)
            min_h = int(H * 0.26)
            if right - left < min_w:
                pad = int(min_w / 2)
                left = max(0, base_left - pad)
                right = min(W, base_right + pad)
            if bottom - top < min_h:
                pad = int(min_h / 2)
                top = max(0, base_top - pad)
                bottom = min(H, base_bottom + pad)

            # Crop for OCR/description and for design reference.
            cropped_img = img.crop((left, top, right, bottom))
            cutout_img = img.crop((base_left, base_top, base_right, base_bottom))

            # Upscale small crops to help OCR read small text.
            try:
                if cropped_img:
                    cw, ch = cropped_img.size
                    target_max = 1600
                    if max(cw, ch) < target_max:
                        scale = target_max / max(cw, ch)
                        nw = max(1, int(cw * scale))
                        nh = max(1, int(ch * scale))
                        cropped_img = cropped_img.resize((nw, nh), Image.LANCZOS)
            except Exception:
                pass
        else:
            cropped_img = img.copy()
            cutout_img = img.copy()

        img.close()

        # [A-Variant] Optionally save the cropped item image for cutout injection
        crop_path = None
        try:
            if save_crop and unique_id is not None and item_index is not None:
                safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(label))[:40]
                crop_filename = f"crop_{unique_id}_{int(item_index):02d}_{safe_label}.png"
                crop_path = os.path.join("outputs", crop_filename)
                if cutout_img:
                    cutout_img.save(crop_path, "PNG")
        except Exception:
            crop_path = None

        prompt = (
            f"Analyze this image cutout of a '{label}'.\n"
            "IMPORTANT: Look specifically at the TEXT written below or near the object.\n"
            "1. **READ EXTRACT DIMENSIONS:** If there is text like 'W: 2800', 'Width 2800mm', '2800*1450', extract these numbers EXACTLY in millimeters.\n"
            "2. Describe: Material, Color, Shape.\n"
            "\n"
            "Return STRICT JSON only:\n"
            "{\n"
            "  \"description\": \"Visual description...\",\n"
            "  \"dimensions_mm\": {\"width\": int/null, \"depth\": int/null, \"height\": int/null},\n"
            "  \"raw_text_found\": \"copy the text you read here\"\n"
            "}\n"
        )
        
        response = call_gemini_with_failover(ANALYSIS_MODEL_NAME, [prompt, cropped_img], {'timeout': 30}, {})
        
        desc = f"A high quality {label}."
        dims_str = ""
        
        if response and response.text:
            data = _safe_extract_json(response.text)
            if data:
                desc = data.get("description", desc)
                raw_dims = data.get("dimensions_mm", {})
                
                # 강제로 description에 치수 정보를 텍스트로 박아넣음 (파싱 로직이 읽을 수 있게)
                w = raw_dims.get("width")
                d = raw_dims.get("depth")
                h = raw_dims.get("height")
                
                if w and d and h:
                    dims_str = f" Dimensions: W={w}mm, D={d}mm, H={h}mm."
                    if LOG_BRIEF:
                        print(f"[Text Read] OK {label}", flush=True)
                    try:
                        _g = SUMMARY_REF.get()
                        if isinstance(_g, dict):
                            _g["text_ok"] = _g.get("text_ok", 0) + 1
                    except Exception:
                        pass
                    if not LOG_BRIEF:
                        print(f"   -> [Text Read] {label}: {dims_str} (Source: {data.get('raw_text_found')})", flush=True)
                else:
                    if LOG_BRIEF:
                        print(f"[Text Read] FAIL {label}", flush=True)
                    try:
                        _g = SUMMARY_REF.get()
                        if isinstance(_g, dict):
                            _g["text_fail"] = _g.get("text_fail", 0) + 1
                    except Exception:
                        pass
                
        if cropped_img:
            try:
                cropped_img.close()
            except Exception:
                pass
        if cutout_img:
            try:
                cutout_img.close()
            except Exception:
                pass
        return {
            "label": label,
            "description": desc + dims_str, # 치수 정보를 설명에 병합
            "box_2d": box,
            "crop_path": crop_path,
        }
            
    except Exception as e:
        print(f"!! Crop Analysis Failed for {item_data.get('label','Furniture')}: {e}", flush=True)
        if cropped_img:
            try:
                cropped_img.close()
            except Exception:
                pass
        if cutout_img:
            try:
                cutout_img.close()
            except Exception:
                pass
    
    return {
        "label": item_data.get('label', 'Furniture'),
        "description": f"A high quality {item_data.get('label','Furniture')}.",
        "box_2d": item_data.get('box_2d'),
        "crop_path": None,
    }

# [최종 복구 및 업그레이드] 분석(Flash) -> 생성(Pro-Image) 2단계 파이프라인
# 구글 AI 스튜디오의 "Generative Reconstruction" 로직 이식
def generate_frontal_room_from_photos(photo_paths, unique_id, index):
    input_images = []
    try:
        print(f"   [Frontal Gen] Step 1: Analyzing {len(photo_paths)} photos with Flash (Spatial Mapping)...", flush=True)
        
        # 1. 이미지 로드
        for path in photo_paths:
            try:
                with Image.open(path) as img:
                    img.thumbnail((1536, 1536))
                    input_images.append(img.copy())
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
    finally:
        for im in input_images:
            try:
                im.close()
            except Exception:
                pass

# [수정] 이미지 편집/데코레이션 처리 로직 (Inpainting & Resizing 강화 버전)
def process_image_edit_logic(photo_paths, instructions, mode, unique_id, index):
    try:
        print(f"   [{mode.upper()}] Processing step with instructions: {instructions}", flush=True)
        
        if not photo_paths: return None
        target_path = photo_paths[0]
        ref_paths = photo_paths[1:7]
        img = None
        ref_imgs = []
        
        try:
            with Image.open(target_path) as base_img:
                base_img.thumbnail((2048, 2048))
                img = base_img.copy()
        except: return None
        try:
            for rp in ref_paths:
                if not rp or not os.path.exists(rp):
                    continue
                with Image.open(rp) as _ref:
                    _ref.thumbnail((2048, 2048))
                    ref_img = _ref.copy()
                    if img:
                        ref_img = pad_image_to_target_canvas(ref_img, img.size[0], img.size[1])
                    ref_imgs.append(ref_img)
        except Exception:
            ref_imgs = []

        # 모드별 시스템 프롬프트 분기
        if mode == 'edit':
            # [EDIT MODE] - Inpainting & Regeneration Focus
            role = "Expert AI Inpainter & Scene Reconstructor."
            task = "Your goal is to MODIFY the scene by ERASING existing objects and REDRAWING them according to the user's size/position requests."
            
            critical_rule = (
                "1. **DESTRUCTIVE EDITING (CRITICAL):** If the user asks to make an object SMALLER, do NOT just shrink it. You MUST **ERASE** the original large object and **REDRAW** a completely new, smaller version in its place.\n"
                "2. **BACKGROUND HALLUCINATION:** When you shrink an object, the wall and floor behind it will be exposed. You MUST **INPAINT** (generate) this missing background texture (wallpaper, skirting board, flooring) seamlessly. Do NOT leave artifacts or the ghost of the old object.\n"
                "3. **AGGRESSIVE SCALE CHANGE:** If the user says 'shrink by 50%' or 'make it small', the new object MUST be visually TINY compared to the original. Do not be subtle. Make the change DRASTIC.\n"
                "4. **ISOLATION:** Ensure the modified object does NOT touch the edges of the room if it's meant to be freestanding. Add empty space on both sides.\n"
                "5. **COLOR/MATERIAL:** Overwrite pixel colors completely if a color change is requested."
            )
            
            # 사용자 지시사항에 '줄여'나 'shrink', 'smaller'가 포함되어 있으면 강제로 강조 문구 추가
            inst_lower = instructions.lower()
            if any(x in inst_lower for x in ['줄여', '작게', 'shrink', 'small', 'reduce', 'tiny']):
                instructions += " (IMPORTANT: The object MUST become significantly smaller. REVEAL the wall/floor behind it.)"

        else:
            # [DECORATE MODE] - 기존 유지
            role = "Expert Home Stager."
            task = "Add decorations and props to the EXISTING room without changing furniture layout."
            critical_rule = (
                "1. **ADDITIVE ONLY:** Do NOT move or remove existing large furniture.\n"
                "2. **PROPS:** Add items like plants, cushions, rugs, lamps, books as requested.\n"
                "3. **STYLE:** Match the lighting and shadow of the original photo perfectly."
            )

        prompt = (
            f"ACT AS: {role}\n"
            f"TASK: {task}\n\n"
            f"<REFERENCE IMAGES>\n"
            "If provided, use them ONLY as material/shape references for the specific objects to be added or replaced.\n"
            "They are NOT a layout or framing guide; do NOT copy their composition or aspect ratio.\n"
            "--------------------------------------------------\n\n"
            f"<USER INSTRUCTIONS (EXECUTE AGGRESSIVELY)>\n"
            f"\"{instructions}\"\n"
            f"--------------------------------------------------\n\n"
            
            f"<CRITICAL RULES>\n"
            f"{critical_rule}\n"
            "4. **FRAMING LOCK (ABSOLUTE):** The output MUST match the target image's framing, composition, and camera viewpoint exactly.\n"
            "5. **ASPECT/SIZE LOCK (ABSOLUTE):** The output MUST be the SAME aspect ratio and resolution as the target image. No cropping, no letterboxing.\n"
            "6. **REFERENCE ROLE:** Reference images are ONLY for object design details; they are composited into the target scene, not re-framed around.\n"
            "7. **INTEGRATION (MODERATE):** Insert reference-based objects into the scene with plausible perspective, floor contact, and soft contact shadows that match the target lighting. Avoid obvious cut-and-paste edges.\n"
            "8. **PADDING IGNORE:** If a reference contains padding/borders, ignore them and use only the object region as a style/shape guide.\n"
            "4. **OUTPUT:** Return a single, high-quality photorealistic image.\n"
            "5. **PHOTOREALISM ONLY:** Output must be indistinguishable from a real photograph.\n"
            "6. **NO CGI / RENDER / ILLUSTRATION:** Avoid any stylized, CGI, or illustrative look.\n"
            "7. **NO TEXT:** Do not add watermarks or text.\n"
            "8. **NO NOISE:** Do NOT add film grain or artificial noise; keep the image clean."
        )

        # 모델 호출 (온도를 살짝 높여서 변화를 유도)
        content = [prompt, "Target image:", img]
        for i, ref in enumerate(ref_imgs):
            content.extend([f"Reference image {i+1}:", ref])
        response = call_gemini_with_failover(MODEL_NAME, content, {'timeout': 90}, {})

        if response and hasattr(response, 'candidates') and response.candidates:
            for part in response.parts:
                if hasattr(part, 'inline_data'):
                    timestamp = int(time.time())
                    out_filename = f"{mode}_{timestamp}_{unique_id}_{index}.png"
                    out_path = os.path.join("outputs", out_filename)
                    with open(out_path, 'wb') as f: f.write(part.inline_data.data)
                    
                    # 해상도/비율 복구
                    final_path = standardize_image_to_target_canvas(out_path, target_path)
                    try:
                        if img:
                            img.close()
                    except Exception:
                        pass
                    return f"/outputs/{os.path.basename(final_path)}"
        try:
            if img:
                img.close()
        except Exception:
            pass
        for rimg in ref_imgs:
            try:
                rimg.close()
            except Exception:
                pass
        return None

    except Exception as e:
        print(f"!! {mode} Gen Error: {e}", flush=True)
        try:
            if img:
                img.close()
        except Exception:
            pass
        for rimg in ref_imgs:
            try:
                rimg.close()
            except Exception:
                pass
        return None

# [NEW] 엔드포인트: 도면 업로드 대신 -> 그냥 사진들만 업로드
@app.post("/generate-frontal-view")
@async_wrap
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
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(generate_frontal_room_from_photos, saved_photo_paths, unique_id, i+1) for i in range(3)]
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

# [NEW] 편집/데코레이션 전용 엔드포인트
@app.post("/generate-image-edit")
@async_wrap
def generate_image_edit_endpoint(
    input_photos: List[UploadFile] = File(...),
    instructions: str = Form(...),
    mode: str = Form(...)  # 'edit' or 'decorate'
):
    try:
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        print(f"\n=== [Image {mode.upper()}] Request: {instructions} ===", flush=True)

        # 1. 사진 저장
        saved_photo_paths = []
        for idx, photo in enumerate(input_photos):
            safe_name = "".join([c for c in photo.filename if c.isalnum() or c in "._-"])
            path = os.path.join("outputs", f"src_{mode}_{timestamp}_{unique_id}_{idx}_{safe_name}")
            with open(path, "wb") as buffer: 
                shutil.copyfileobj(photo.file, buffer)
            saved_photo_paths.append(path)
        
        generated_results = []
        
        # 2. 생성 (단일 이미지 처리)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(process_image_edit_logic, saved_photo_paths, instructions, mode, unique_id, i+1) for i in range(1)]
            for future in futures:
                res = future.result()
                if res: generated_results.append(res)
        
        if generated_results:
            return JSONResponse(content={"urls": generated_results, "message": "Success"})
        else:
            return JSONResponse(content={"error": "Failed to generate image"}, status_code=500)
            
    except Exception as e:
        print(f"🔥🔥🔥 [Error] {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)
# -----------------------------------------------------------------------------
# Generation Logic
# -----------------------------------------------------------------------------

def generate_empty_room(image_path, unique_id, start_time, stage_name="Stage 1"):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return image_path
    log_step(f"[{stage_name}] Empty Room Generation ({MODEL_NAME})")
    
    img = Image.open(image_path)
    system_instruction = "You are an expert architectural AI."
    
    prompt = (
        "IMAGE EDITING TASK: Extreme Cleaning & Architectural Restoration.\n\n"
        "<CRITICAL: STRUCTURAL PRESERVATION (PRIORITY #0)>\n"
        "1. **DO NOT CHANGE ARCHITECTURE:** Preserve room layout, walls, ceiling, floor, built-ins, and openings exactly as-is.\n"
        "2. **DO NOT MOVE THE CAMERA:** Keep viewpoint, perspective, lens, and framing identical to the input image.\n"
        "3. **DO NOT ALTER MATERIALS:** Keep wall finishes, flooring, baseboards, trims, and ceiling details unchanged.\n"
        "4. **DO NOT ALTER LIGHTING/SHADOWS:** Keep natural lighting direction, intensity, and window light pattern consistent.\n"
        "5. **DO NOT REMOVE FIXTURES:** Strictly preserve structural elements including Columns, Pillars, Beams, Windows (frames & glass), Doors, and built-in fireplaces.\n"
        "6. **VIEW PROTECTION:** Keep the view outside the window 100% original.\n\n"
        "7. **ONLY REMOVE MOVABLES:** Only remove furniture, rugs, lightings, curtains, and decorations that are NOT part of the building structure.\n\n"
        
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
                        try:
                            img.close()
                        except Exception:
                            pass
                        return standardize_image_to_reference_canvas(path, image_path)
            else:
                print(f"⚠️ [Blocked] 안전 필터 차단", flush=True)
        print(f"⚠️ [Retry] 시도 {try_count+1} 실패. 재시도...", flush=True)

    print(">> [실패] 빈 방 생성 불가. 원본 사용.", flush=True)
    try:
        img.close()
    except Exception:
        pass
    return image_path

# [수정] 원본 프롬프트 유지 + 비율 자동 감지 + 텍스트/여백 금지 + 무드보드 비율 무시 + 공간 제약 사항 추가
def generate_furnished_room(
    room_path,
    style_prompt,
    ref_path,
    unique_id,
    furniture_specs=None,
    furniture_specs_json=None,
    room_dimensions=None,
    placement_instructions=None,
    scale_guide_path=None,
    primary_item=None,
    room_dims_parsed=None,
    wall_span_norm=None,
    size_hierarchy=None,
    start_time=0,
    room_planes=None,
):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT: return None
    room_img = None
    extra_imgs = []
    try:
        room_img = Image.open(room_path)
        
        # [NEW] 이미지 비율 계산 (가로형/세로형 판단)
        width, height = room_img.size
        is_portrait = height > width
        ratio_instruction = "PORTRAIT (4:5 Ratio)" if is_portrait else "LANDSCAPE (16:9 Ratio)"
        expected_ratio = (4 / 5) if is_portrait else (16 / 9)
        ratio_tol = 0.05
        
        system_instruction = "You are an expert interior designer AI."
        
        specs_context = ""
        if furniture_specs:
            specs_context = (
                "\n<REFERENCE FURNITURE LIST (EXACT MATCH)>\n"
                "The following list describes the items detected from the moodboard.\n"
                "You MUST match the exact design, shape, material, and color of each item.\n"
                "Do NOT copy the moodboard layout. Do NOT add extra items. Do NOT omit any listed items.\n"
                "Do NOT replace any listed item with a generic substitute (no sofa instead of a desk, etc.).\n"
                f"{furniture_specs}\n"
                "--------------------------------------------------\n"
            )

        # [A-Variant] Add a strict dimension table (mm) for ALL items when available.
        dims_table_context = ""
        try:
            if furniture_specs_json and isinstance(furniture_specs_json, dict):
                rows = []
                for it in (furniture_specs_json.get("items") or []):
                    lbl = (it.get("label") or "").strip()
                    dm = it.get("dims_mm") or {}
                    w = dm.get("width_mm"); d = dm.get("depth_mm"); h = dm.get("height_mm")
                    if any([w, d, h]):
                        rows.append(f"- {lbl}: W={w or 'null'}mm, D={d or 'null'}mm, H={h or 'null'}mm")
                if rows:
                    dims_table_context = (
                        "\n<FURNITURE DIMENSIONS TABLE (MM) - STRICT>\n"
                        "Use these real-world measurements. Do NOT invent new sizes.\n"
                        "Items with null W/D/H are incomplete; do NOT guess missing numbers. Use moodboard scale and keep within room limits.\n"
                        + "\n".join(rows) + "\n"
                        "Hard constraints:\n"
                        "- No furniture item may exceed room width or room depth.\n"
                        "- Rugs/carpets: if rug width is within 10% of room width, it must visually span almost wall-to-wall.\n"
                        "- Wall storage/sideboard: if width is <= 1500mm in specs, it must NOT look like it spans most of the wall.\n"
                        "--------------------------------------------------\n"
                    )
        except Exception:
            dims_table_context = ""

        # [NEW] 공간 제약 사항 및 SCALE FIX 계산 로직 강화
        spatial_context = ""
        calculated_analysis = ""
        ratio_rules_context = ""
        incomplete_dims_context = ""
        inventory_context = ""

        try:
            _room_dims = room_dims_parsed or parse_room_dimensions_mm(room_dimensions or "")
            room_w = int(_room_dims.get("width_mm") or 0)
            room_d = int(_room_dims.get("depth_mm") or 0)
            room_h = int(_room_dims.get("height_mm") or 0)

            _primary = primary_item or (furniture_specs_json or {}).get("primary") or {}
            _p_dims = _primary.get("dims_mm") or {}
            p_w = int(_p_dims.get("width_mm") or 0)
            p_d = int(_p_dims.get("depth_mm") or 0)
            p_h = int(_p_dims.get("height_mm") or 0)

            # Primary width fallback if missing
            if not p_w and furniture_specs_json and isinstance(furniture_specs_json, dict):
                try: p_w = int(furniture_specs_json.get("max_width_mm") or 0)
                except Exception: pass

            # Build W/D/H ratio rules for all items with complete dims
            try:
                if furniture_specs_json and isinstance(furniture_specs_json, dict):
                    complete_items = []
                    incomplete_items = []
                    inventory_labels = []

                    for it in (furniture_specs_json.get("items") or []):
                        label = (it.get("label") or "").strip()
                        if not label:
                            label = "Unknown Item"
                        inventory_labels.append(label)
                        dm = it.get("dims_mm") or {}
                        w = int(dm.get("width_mm") or 0)
                        d = int(dm.get("depth_mm") or 0)
                        h = int(dm.get("height_mm") or 0)
                        missing = []
                        if w <= 0: missing.append("W")
                        if d <= 0: missing.append("D")
                        if h <= 0: missing.append("H")
                        if missing:
                            incomplete_items.append((label, missing))
                            if LOG_BRIEF:
                                print(f"[Dims] FAIL {label} missing {','.join(missing)}", flush=True)
                            try:
                                _g = SUMMARY_REF.get()
                                if isinstance(_g, dict):
                                    _g["dims_fail"] = _g.get("dims_fail", 0) + 1
                            except Exception:
                                pass
                            continue
                        complete_items.append({"label": label, "w": w, "d": d, "h": h})

                    if incomplete_items:
                        incomplete_dims_context = (
                            "\n<INCOMPLETE DIMENSIONS (DO NOT IGNORE)>\n"
                            + "\n".join([f"- {lbl}: missing {', '.join(miss)}" for lbl, miss in incomplete_items]) + "\n"
                            + "Rule: Do NOT invent missing numbers, but you MUST still render these items.\n"
                            + "Estimate size from the moodboard and keep within room limits and relative proportions.\n"
                            + "--------------------------------------------------\n"
                        )

                        if inventory_labels:
                            inventory_context = (
                                "\n<ITEM INVENTORY (MUST RENDER ALL ITEMS)>\n"
                                f"Total items: {len(inventory_labels)}\n"
                                + "\n".join([f"- {lbl}" for lbl in inventory_labels]) + "\n"
                                "Rule: Every listed item must appear in the final image (exactly once unless the list says multiples).\n"
                                "If space is tight, reduce size slightly and place items on shelves/tables or walls; do not omit.\n"
                                "--------------------------------------------------\n"
                            )

                    def _ratio_str(value, total, cap=None):
                        if not value or not total:
                            return "n/a"
                        pct = round((value / total) * 100, 1)
                        if cap is not None and pct > cap:
                            return f"{cap:.1f}% (cap)"
                        return f"{pct:.1f}%"

                    abs_lines = []
                    abs_warn_labels = []
                    if room_w > 0 and room_d > 0 and room_h > 0:
                        for it in complete_items:
                            w = it["w"]; d = it["d"]; h = it["h"]; label = it["label"]
                            abs_lines.append(
                                f"- {label}: room W={_ratio_str(w, room_w, 100.0)}, D={_ratio_str(d, room_d, 100.0)}, H={_ratio_str(h, room_h, 100.0)}"
                            )
                            over = []
                            if w > room_w: over.append("W")
                            if d > room_d: over.append("D")
                            if h > room_h: over.append("H")
                            if over:
                                abs_warn_labels.append(label)
                            try:
                                _g = SUMMARY_REF.get()
                                if isinstance(_g, dict):
                                    _g["dims_warn"] = _g.get("dims_warn", 0) + 1
                            except Exception:
                                pass
                    else:
                        if LOG_BRIEF and not LOG_SUMMARY:
                            print("[Dims] WARN room W/D/H missing; skip absolute ratios", flush=True)
                        try:
                            _g = SUMMARY_REF.get()
                            if isinstance(_g, dict):
                                _g["dims_warn"] = _g.get("dims_warn", 0) + 1
                        except Exception:
                            pass

                    rel_lines = []
                    rel_warn_labels = []
                    primary_label = _primary.get('label', 'Primary Furniture')
                    if p_w > 0 and p_d > 0 and p_h > 0:
                        for it in complete_items:
                            label = it["label"]
                            if label == primary_label:
                                continue
                            rel_w = round((it["w"] / p_w) * 100, 1)
                            rel_d = round((it["d"] / p_d) * 100, 1)
                            rel_h = round((it["h"] / p_h) * 100, 1)
                            rel_lines.append(
                                f"- {label}: W={rel_w:.1f}%, D={rel_d:.1f}%, H={rel_h:.1f}% of {primary_label}"
                            )
                            if rel_w > 100 or rel_d > 100 or rel_h > 100:
                                rel_warn_labels.append(label)
                            try:
                                _g = SUMMARY_REF.get()
                                if isinstance(_g, dict):
                                    _g["dims_warn"] = _g.get("dims_warn", 0) + 1
                            except Exception:
                                pass
                    else:
                        if LOG_BRIEF:
                            print("[Dims] WARN primary W/D/H missing; skip relative ratios", flush=True)
                    if LOG_BRIEF and not LOG_SUMMARY:
                        if abs_warn_labels:
                            sample = ", ".join(abs_warn_labels[:3])
                            extra = len(abs_warn_labels) - 3
                            suffix = f" (+{extra} more)" if extra > 0 else ""
                            print(f"[Dims] WARN {len(abs_warn_labels)} items exceed room W/D/H: {sample}{suffix}", flush=True)
                        if rel_warn_labels:
                            sample = ", ".join(rel_warn_labels[:3])
                            extra = len(rel_warn_labels) - 3
                            suffix = f" (+{extra} more)" if extra > 0 else ""
                            print(f"[Dims] WARN {len(rel_warn_labels)} items larger than primary: {sample}{suffix}", flush=True)

                    order_w = ""
                    order_d = ""
                    order_h = ""
                    if complete_items:
                        order_w = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["w"], reverse=True)])
                        order_d = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["d"], reverse=True)])
                        order_h = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["h"], reverse=True)])

                    height_caps = []
                    for it in complete_items:
                        h = it["h"]
                        if h > 0:
                            height_caps.append(f"- {it['label']}: H must be <= {h}mm")

                    if abs_lines or rel_lines or order_w or order_d or order_h:
                        ratio_rules_context = (
                            "\n<CRITICAL: W/D/H RATIO RULES (ALL FURNITURE)>\n"
                            "Apply ratios only to items with complete W/D/H.\n"
                        )
                        if abs_lines:
                            ratio_rules_context += (
                                "ABSOLUTE RATIOS (item vs room):\n"
                                + "\n".join(abs_lines) + "\n"
                            )
                        else:
                            ratio_rules_context += "ABSOLUTE RATIOS: room W/D/H missing or invalid.\n"
                        if rel_lines:
                            ratio_rules_context += (
                                f"RELATIVE RATIOS (item vs {primary_label}):\n"
                                + "\n".join(rel_lines) + "\n"
                            )
                        if order_w or order_d or order_h:
                            ratio_rules_context += (
                                "DIMENSION ORDER (largest -> smallest):\n"
                                + f"- WIDTH: {order_w}\n"
                                + f"- DEPTH: {order_d}\n"
                                + f"- HEIGHT: {order_h}\n"
                            )
                        if height_caps:
                            ratio_rules_context += (
                                "HEIGHT CAPS (STRICT):\n"
                                + "\n".join(height_caps) + "\n"
                            )
                        ratio_rules_context += "--------------------------------------------------\n"
            except Exception:
                pass

            if room_w > 0 and p_w > 0:
                occ = round((p_w / room_w) * 100, 1)

                # Total gap across both sides
                gap_total_mm = room_w - p_w
                gap_side_mm = int(gap_total_mm / 2) if gap_total_mm > 0 else 0

                primary_d_disp = f"{p_d}mm" if p_d > 0 else "unknown"
                primary_h_disp = f"{p_h}mm" if p_h > 0 else "unknown"
                room_d_disp = f"{room_d}mm" if room_d > 0 else "unknown"
                room_h_disp = f"{room_h}mm" if room_h > 0 else "unknown"

                calculated_analysis += (
                    f"   - **PRIMARY ANCHOR:** {_primary.get('label','Primary Furniture')} "
                    f"(W {p_w}mm, D {primary_d_disp}, H {primary_h_disp})\n"
                )
                calculated_analysis += f"   - **ROOM DIMS:** W {room_w}mm, D {room_d_disp}, H {room_h_disp}\n"
                calculated_analysis += f"   - **CALCULATED GAP (WIDTH):** Total empty space width = {gap_total_mm}mm. (approx {gap_side_mm}mm on each side).\n"
                calculated_analysis += f"   - **WIDTH OCCUPANCY:** {occ}% (The furniture takes up {occ}% of the wall).\n"

                if occ > 92:
                    calculated_analysis += "   - **ACTION: WALL-TO-WALL FIT.** The furniture is almost as wide as the room. It must TOUCH the side walls or have negligible gaps.\n"
                elif occ > 80:
                    calculated_analysis += "   - **ACTION: TIGHT FIT.** The furniture dominates the wall. Leave only SMALL gaps on the sides.\n"
                else:
                    calculated_analysis += "   - **ACTION: STANDARD FIT.** Center the furniture with visible breathing room on sides.\n"

            if room_d > 0 and p_d > 0:
                depth_occ = round((p_d / room_d) * 100, 1)
                calculated_analysis += f"   - **DEPTH OCCUPANCY:** {depth_occ}% (Floor depth usage).\n"

            if room_h > 0 and p_h > 0:
                height_occ = round((p_h / room_h) * 100, 1)
                calculated_analysis += f"   - **HEIGHT OCCUPANCY:** {height_occ}% (Height usage).\n"

            if room_w <= 0 or p_w <= 0:
                calculated_analysis += "   - (No reliable W/D/H dimensions found; apply relative scaling from reference hierarchy)\n"

        except Exception:
            pass
        if room_dimensions or placement_instructions:
            spatial_context = "\n<PHYSICAL SPACE CONSTRAINTS (STRICT ADHERENCE)>\n"
            if room_dimensions:
                spatial_context += f"- **ACTUAL ROOM DIMENSIONS:** {room_dimensions}\n"
            if placement_instructions:
                spatial_context += f"- **PLACEMENT INSTRUCTIONS:** {placement_instructions}\n"
            spatial_context += (
                "**SCALING RULE:** You MUST calibrate the scale of all furniture relative to the ACTUAL ROOM DIMENSIONS provided.\n"
                f"{calculated_analysis}\n" # 계산된 분석 결과 삽입
                "Do NOT shrink furniture to create artificial empty space. If the room is small, it should look appropriately filled.\n"
                "--------------------------------------------------\n"
            )

        # [NEW] hierarchy hint string
        size_hierarchy_hint = ""
        try:
            if size_hierarchy and isinstance(size_hierarchy, list):
                size_hierarchy_hint = " > ".join([str(x) for x in size_hierarchy if x])
            elif furniture_specs_json and isinstance(furniture_specs_json, dict):
                h = furniture_specs_json.get("size_hierarchy") or []
                if isinstance(h, list):
                    size_hierarchy_hint = " > ".join([str(x) for x in h if x])
        except Exception:
            size_hierarchy_hint = ""


        user_original_prompt = (
            "IMAGE MANIPULATION TASK (Virtual Staging - Overlay Only):\n"
            "Your goal is to PLACE furniture into the EXISTING empty room image without changing the room itself.\n\n"
            
            "<CRITICAL: ARCHITECTURAL FREEZE (PRIORITY #1)>\n"
            "1. **DO NOT RE-GENERATE THE ROOM:** The walls, ceiling, floor pattern, window size, and view outside the window must remain 100% IDENTICAL to the input image.\n"
            "2. **PERSPECTIVE LOCK:** You must use the EXACT same camera angle and perspective. Do not zoom in, do not zoom out.\n"
            "3. **DEPTH PRESERVATION:** Do not expand the room. Keep the original spatial depth.\n"
            "4. **FRAMING LOCK:** Keep the full room framing. Do NOT crop to a close-up. The ceiling and floor edges must match the input.\n"
            "5. **CORNER VISIBILITY:** Both left and right wall corners must remain visible, matching the input framing.\n\n"
            
            "<CRITICAL: FURNITURE COMPOSITING>\n"
            "1. **SCALE:** Fit furniture realistically within the *existing* floor space.\n"
            "2. **PLACEMENT:** Place items *on* the floor. Ensure legs touch the ground with correct contact shadows.\n"
            "3. **STYLE:** Match the Reference Moodboard style.\n"
            "4. **ONLY LISTED ITEMS:** Render only the listed items. Do NOT add extra furniture or swap designs.\n"
            "5. **WINDOW TREATMENT (CURTAINS - LOCATION STRICT):** Add floor-to-ceiling **Sheer White Chiffon Curtains**. <CRITICAL>: Place them **ONLY** along the vertical edges of the GLASS WINDOW. **DO NOT** generate curtains on solid walls, corners without windows, or doors. They must **HANG STRAIGHT DOWN NATURALLY** (do not tie) covering only the outer 15% of the glass to frame the view.\n\n"

            "<CRITICAL: MATHEMATICAL SCALE ENFORCEMENT (PRIORITY #0)>\nYou are provided with ACTUAL DIMENSIONS, PRIMARY ANCHOR, and (optionally) a W/D/H SCALE GUIDE IMAGE. Do not ignore them.\nIMPORTANT: The 'PRIMARY ANCHOR' is the largest-volume movable furniture (EXCLUDING rugs/carpets).\nSIZE HIERARCHY (largest -> smallest, exclude rugs/carpets): {size_hierarchy_hint}\n\n"
            "You are provided with ACTUAL DIMENSIONS and PRE-CALCULATED RATIOS. Do not ignore them.\n"
            
            "1. **SPECIFIC SCALE ANALYSIS FOR THIS REQUEST:**\n"
            f"{calculated_analysis if calculated_analysis else '   - (Apply relative scaling based on provided specs)'}\n"
            
            "2. **RELATIVE W/D/H HIERARCHY:**\n"
            "   - You MUST maintain the visual width/depth/height hierarchy specified in the specs.\n"
            "   - Example: If Item A (H: 950mm) is taller than Item B (H: 775mm), Item A MUST be rendered taller than Item B in the image.\n"
            
            "3. **RATIO LOCK:**\n"
            "   - Calculate: (Furniture W/D/H) / (Room W/D/H) = Coverage ratios.\n"
            "   - Strictly follow these percentages. Do not shrink items into 'miniature' versions to create empty space.\n"
            "   - **STRICT PROHIBITION:** Do not resize items for 'vibe' or 'aesthetic balance'. Follow the NUMBERS strictly.\n"
            "4. **HEIGHT CONSISTENCY:**\n"
            "   - Do NOT make a shorter item appear taller by placing it closer to the camera.\n"
            "   - Apparent height must respect the real H ratios across all items.\n"

            "<CRITICAL: WINDOW LIGHT MUST BE ABUNDANT (PRIORITY #1)>\n"
            "1. **ABUNDANT WINDOW LIGHT:** The scene MUST be strongly illuminated by abundant daylight coming from the window.\n"
            "2. **EXPOSURE RULE:** Bright and airy (not dark), while preserving highlight detail (no blown-out whites).\n"
            "3. **LIGHT DIRECTION:** Clearly visible light direction from the window; cast soft but present shadows across the floor.\n"
            "4. **NO DIM ROOM:** Do NOT generate a dim, underexposed, moody, or nighttime look.\n"
            "5. **WHITE BALANCE:** Neutral daylight white balance (around 4000~5000K). **NO warm/yellow cast.**\n\n"

        "<CRITICAL: PHOTOREALISTIC LIGHTING INTEGRATION (HYBRID: DAYLIGHT + ARTIFICIAL)>\n"
            "1. **LIGHTING STATE: SUBTLE SUPPORT ONLY (NEUTRAL):**\n"
            "   - **ACTION:** Keep interior fixtures ON only if they appear in the reference; no extra fixtures.\n"
            "   - **VISUALS:** Avoid visible glow/bloom halos. Lights should look realistic and restrained.\n"
            
            "2. **LIGHTING HIERARCHY (KEY vs. FILL):**\n"
            "   - **KEY LIGHT (DOMINANT):** Natural daylight from the window is the PRIMARY source (approx. 80% intensity).\n"
            "   - **FILL LIGHT (SECONDARY):** Interior lights act as gentle fill (approx. 20%). They must NOT overpower the daylight.\n"
            
            "3. **STRICT COLOR TEMPERATURE CONTROL (NO YELLOW):**\n"
            "   - **Target Temperature:** Use **Neutral White (4000K-5000K)** for any artificial lights to match daylight.\n"
            "   - **PROHIBITED:** No warm/tungsten/orange bulbs (2700K). No vintage/sepia cast.\n"
            
            "4. **SHADOW PHYSICS:**\n"
            "   - Cast soft, directional shadows driven by window light.\n"
            "   - Use interior lights only to lift the darkest corners slightly.\n"
            
            "5. **ATMOSPHERE:**\n"
            "   - Bright and airy, but never overlit. Preserve highlight detail and avoid glare.\n"
            "   - **OUTPUT RULE:** Return the image with furniture added, blended with abundant daylight and subtle interior lighting.\n"
        )
        
        prompt = (
            "ACT AS: Professional Interior Photographer.\n"
            f"{specs_context}\n" 
            f"{dims_table_context}\n"
            f"{incomplete_dims_context}\n"
            f"{spatial_context}\n"
            f"{inventory_context}\n"
            f"{ratio_rules_context}\n"
            f"{user_original_prompt}\n\n"
            
            f"<CRITICAL: OUTPUT FORMAT ENFORCEMENT -> {ratio_instruction}>\n"
            "1. **FULL BLEED CANVAS:** The output image MUST fill the entire canvas from edge to edge. **NO WHITE BARS.** NO SPLIT SCREENS.\n"
            "2. **NO TEXT OVERLAY:** Do NOT write any dimensions, labels, or watermarks on the final image. It must be a clean photo.\n"
            "3. **ASPECT RATIO LOCK (HARD):** You MUST output EXACTLY " + ratio_instruction + ". Any other ratio is invalid.\n"
            "4. **NO PORTRAIT FOR LANDSCAPE INPUTS:** If the input is landscape, output must remain landscape (16:9). Never generate portrait.\n"
            "5. **NO LANDSCAPE FOR PORTRAIT INPUTS:** If the input is portrait, output must remain portrait (4:5). Never generate landscape.\n"
            "6. **IGNORE REFERENCE RATIO:** Even if the Style Reference (Moodboard) is vertical, you MUST output a " + ratio_instruction + " image. Do not mimic the moodboard's shape.\n"
            "7. **NO MULTI-PANEL OUTPUT:** Output must be ONE single staged room photograph only. Do NOT append catalog sheets, white inventory panels, split layouts, or include the reference image anywhere."
        )
        
        prompt = prompt.replace("{size_hierarchy_hint}", size_hierarchy_hint or "")

        content = [prompt, "Empty Room (Target Canvas - KEEP THIS):", room_img]

        # [A-Variant] Provide cropped furniture cutouts
        try:
            if furniture_specs_json and isinstance(furniture_specs_json, dict):
                cutouts = []
                for it in (furniture_specs_json.get("items") or []):
                    cp = it.get("crop_path")
                    lbl = (it.get("label") or "").strip()
                    if cp and os.path.exists(cp):
                        cutouts.append((lbl, cp))
                for lbl, cp in cutouts[:10]:
                    cutout_img = Image.open(cp)
                    extra_imgs.append(cutout_img)
                    content += [f"Furniture Cutout Reference (MUST MATCH EXACT DESIGN). Label: {lbl}", cutout_img]
        except Exception:
            pass

        try:
            if scale_guide_path and os.path.exists(scale_guide_path):
                guide_img = Image.open(scale_guide_path)
                extra_imgs.append(guide_img)
                content += ["SCALE GUIDE IMAGE (W/D/H guide; do NOT render; use only for measurement):", guide_img]
        except Exception:
            pass
        if ref_path:
            try:
                ref = Image.open(ref_path)
                ref.thumbnail((2048, 2048))
                extra_imgs.append(ref)
                content.extend(["Style Reference (Exact Furniture Designs):", ref])
            except: pass
        
        remaining = max(30, TOTAL_TIMEOUT_LIMIT - (time.time() - start_time))
        
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        def _render_once():
            response = call_gemini_with_failover(MODEL_NAME, content, {'timeout': remaining}, safety_settings, system_instruction)
            if response and hasattr(response, 'candidates') and response.candidates and hasattr(response, 'parts'):
                for part in response.parts:
                    if hasattr(part, 'inline_data'):
                        timestamp = int(time.time())
                        filename = f"result_{timestamp}_{unique_id}.png"
                        path = os.path.join("outputs", filename)
                        with open(path, 'wb') as f: f.write(part.inline_data.data)
                        try:
                            with Image.open(path) as _chk:
                                w, h = _chk.size
                            if h <= 0:
                                return None
                            r = w / h
                            if abs(r - expected_ratio) > ratio_tol:
                                if LOG_BRIEF:
                                    print(f"[RatioCheck] FAIL {w}x{h} (expected ~{expected_ratio:.4f})", flush=True)
                                return None
                        except Exception:
                            return None
                        return standardize_image_to_reference_canvas(path, room_path)
            return None

        max_attempts = 3
        last_path = None
        for attempt in range(max_attempts):
            last_path = _render_once()
            if not last_path:
                continue

            if SCALE_CHECK and furniture_specs_json and room_dims_parsed and room_planes:
                ok, issues = validate_furnished_scale(
                    last_path,
                    furniture_specs_json,
                    room_dims_parsed,
                    room_planes,
                    primary_label=(primary_item or {}).get("label"),
                )
                if not ok:
                    if LOG_BRIEF:
                        print(f"[ScaleCheck] FAIL attempt {attempt+1}/{max_attempts}: {', '.join(issues)}", flush=True)
                    else:
                        logger.warning(f"[ScaleCheck] FAIL attempt {attempt+1}/{max_attempts}: {issues}")
                    if attempt < max_attempts - 1:
                        continue
            return last_path
        return last_path
    except Exception as e:
        print(f"!! Stage 2 에러: {e}", flush=True)
        return None
    finally:
        for im in extra_imgs:
            try:
                im.close()
            except Exception:
                pass
        try:
            if room_img:
                room_img.close()
        except Exception:
            pass

def call_magnific_api(image_path, unique_id, start_time):
    if time.time() - start_time > TOTAL_TIMEOUT_LIMIT:
        return image_path

    print(f"\n--- [Stage 4] Magnific Upscaling (Key: {MAGNIFIC_API_KEY[:5]}...) ---", flush=True)

    if not MAGNIFIC_API_KEY or "your_" in MAGNIFIC_API_KEY:
        print(">> [SKIP] API key missing. Return original.", flush=True)
        return image_path

    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')

        payload = {
            "image": b64,
            "scale_factor": "2x",
            "optimized_for": "films_n_photography",
            "engine": "automatic",
            "creativity": 0,
            "hdr": 0,
            "resemblance": 10,
            "fractality": 0,
            "prompt": (
                "Professional interior photography, architectural digest style, "
                "shot on Phase One XF IQ4, 100mm lens, ISO 100, f/8, "
                "natural white daylight coming from window, soft shadows, "
                "clean textures, true-to-source details, raw photo, 8k resolution. "
                "--no 3d render, cgi, painting, drawing, cartoon, anime, illustration, plastic look, oversaturated, watermark, text, blur, distorted."
            ),
        }
        headers = {
            "x-freepik-api-key": MAGNIFIC_API_KEY,
            "Content-Type": "application/json",
        }

        res = requests.post(MAGNIFIC_ENDPOINT, json=payload, headers=headers)
        if res.status_code != 200:
            print(f"!! [API Error] Status: {res.status_code}, Msg: {res.text}", flush=True)
            return image_path

        data = res.json()
        if "data" not in data:
            return image_path

        if "task_id" in data["data"]:
            task_id = data["data"]["task_id"]
            print(f">> Task queued (ID: {task_id})...", end="", flush=True)

            while time.time() - start_time < TOTAL_TIMEOUT_LIMIT:
                time.sleep(2)
                print(".", end="", flush=True)

                check = requests.get(f"{MAGNIFIC_ENDPOINT}/{task_id}", headers=headers)
                if check.status_code == 200:
                    status_data = check.json().get("data", {})
                    status = status_data.get("status")

                    if status == "COMPLETED":
                        print(" done!", flush=True)
                        gen_list = status_data.get("generated", [])
                        if gen_list:
                            return download_image(gen_list[0], unique_id) or image_path
                    elif status == "FAILED":
                        print(" failed.", flush=True)
                        return image_path
            return image_path

        elif "generated" in data.get("data", {}):
            gen_list = data["data"]["generated"]
            if gen_list:
                return download_image(gen_list[0], unique_id) or image_path

        return image_path

    except Exception:
        return image_path


def pad_image_to_target_canvas(
    img: Image.Image,
    target_w: int,
    target_h: int,
    pad_color: tuple = (255, 255, 255),
) -> Image.Image:
    """Pad (and only downscale if needed) to match the target canvas size."""
    try:
        if target_w <= 0 or target_h <= 0:
            return img
        w, h = img.size
        if w <= 0 or h <= 0:
            return img

        scale = min(1.0, target_w / w, target_h / h)
        if scale < 1.0:
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            w, h = img.size

        canvas = Image.new('RGB', (target_w, target_h), pad_color)
        x0 = max(0, (target_w - w) // 2)
        y0 = max(0, (target_h - h) // 2)
        canvas.paste(img, (x0, y0))
        return canvas
    except Exception:
        return img

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
@async_wrap
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
async def favicon(): return FileResponse("static/favicon-light.png")

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
@async_wrap
def render_room(
    file: UploadFile = File(...), 
    room: str = Form(...), 
    style: str = Form(...), 
    variant: str = Form(...),
    moodboard: UploadFile = File(None),
    dimensions: str = Form(""),
    placement: str = Form("")
):
    summary_token = None
    try:
        unique_id = uuid.uuid4().hex[:8]
        log_section(f"REQUEST START [{unique_id}] (Integrated Analysis Mode)")
        start_time = time.time()
        summary = {
            'text_ok': 0,
            'text_fail': 0,
            'dims_fail': 0,
            'dims_warn': 0,
            'scalecheck_fail': 0,
            'scale_guide_skipped': 0,
        }
        summary_token = SUMMARY_REF.set(summary)
        
        timestamp = int(time.time())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
        raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{safe_name}")
        with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
        
        std_path = standardize_image(raw_path)
        step1_img = generate_empty_room(std_path, unique_id, start_time, stage_name="Stage 1: Intermediate Clean")

        # [SCALE FIX vB] Precompute room dimensions + back wall span (for scale lock & auto-pick)
        room_dims_parsed = parse_room_dimensions_mm(dimensions or "")
        room_planes = detect_room_planes_norm(step1_img) if step1_img else None
        if room_planes:
            wall_span_norm = (room_planes.get("x_left", 0.0), room_planes.get("x_right", 1.0))
        else:
            wall_span_norm = detect_back_wall_span_norm(step1_img) if step1_img else (0.0, 1.0)

        furniture_specs_json = None
        primary_item = None
        scale_guide_path = None
        size_hierarchy = None
        
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
            if not LOG_BRIEF:
                print(f">> [Global Analysis] Analyzing furniture in {ref_path}...", flush=True)
            try:
                detected = detect_furniture_boxes(ref_path)
                
                if not LOG_BRIEF:
                    print(f">> [Global Analysis] Parallel analyzing {len(detected)} items...", flush=True)
                with ThreadPoolExecutor(max_workers=30) as executor:
                    futures = [
                        executor.submit(analyze_cropped_item, ref_path, item, unique_id, idx + 1, True)
                        for idx, item in enumerate(detected)
                    ]
                    full_analyzed_data = [f.result() for f in futures]
                try:
                    if full_analyzed_data and not LOG_BRIEF:
                        logger.info(f"[Analysis] items={len(full_analyzed_data)}")
                        for i, it in enumerate(full_analyzed_data[:30]):
                            dims = parse_object_dimensions_mm(it.get("description",""))
                            logger.info(
                                f"[Analysis] #{i} {it.get('label')} "
                                f"dims(mm) W={dims.get('width_mm')} D={dims.get('depth_mm')} H={dims.get('height_mm')} "
                                f"crop={it.get('crop_path')} "
                                f"desc={ (it.get('description','')[:120]).replace('\\n',' ') }"
                            )
                except Exception:
                    logger.exception("[Analysis] logging failed")
                
                specs_list = []
                for idx, item in enumerate(full_analyzed_data):
                    specs_list.append(f"{idx+1}. {item['label']}: {item['description']}")
                furniture_specs_text = "\n".join(specs_list)

                # [SCALE FIX vB] Build furniture JSON + select PRIMARY (largest volume, exclude rugs/carpets)
                try:
                    furniture_specs_json = build_furniture_specs_json(full_analyzed_data)
                    primary_item = (furniture_specs_json or {}).get("primary")
                    size_hierarchy = (furniture_specs_json or {}).get("size_hierarchy")

                    logger.info(f"[Scale] primary_item={ (primary_item or {}).get('label') }")
                    logger.info(f"[Scale] room_dims_parsed={room_dims_parsed} wall_span_norm={wall_span_norm}")

                    # Optional: W/D/H scale guide image (requires room dims + complete item dims + room planes)
                    try:
                        room_w = int((room_dims_parsed or {}).get("width_mm") or 0)
                        room_d = int((room_dims_parsed or {}).get("depth_mm") or 0)
                        room_h = int((room_dims_parsed or {}).get("height_mm") or 0)

                        p_w = int(((primary_item or {}).get("dims_mm") or {}).get("width_mm") or 0)
                        p_d = int(((primary_item or {}).get("dims_mm") or {}).get("depth_mm") or 0)
                        p_h = int(((primary_item or {}).get("dims_mm") or {}).get("height_mm") or 0)
                        scale_anchor_label = (primary_item or {}).get("label")

                        if not p_w and furniture_specs_json and isinstance(furniture_specs_json, dict):
                            try:
                                p_w = int(furniture_specs_json.get("max_width_mm") or 0)
                            except Exception:
                                pass

                        if (not p_w or not p_d or not p_h) and furniture_specs_json and isinstance(furniture_specs_json, dict):
                            best = None
                            for it in (furniture_specs_json.get("items") or []):
                                if it.get("is_rug"):
                                    continue
                                dm = it.get("dims_mm") or {}
                                w = int(dm.get("width_mm") or 0)
                                d = int(dm.get("depth_mm") or 0)
                                h = int(dm.get("height_mm") or 0)
                                if w and d and h:
                                    vp = int(it.get("volume_proxy") or (w * d * h))
                                    if (best is None) or (vp > best[0]):
                                        best = (vp, it, w, d, h)
                            if best:
                                _, best_item, w, d, h = best
                                p_w, p_d, p_h = w, d, h
                                scale_anchor_label = best_item.get("label") or scale_anchor_label

                        logger.info(
                            f"[Scale] room_w={room_w}mm room_d={room_d}mm room_h={room_h}mm "
                            f"p_w={p_w}mm p_d={p_d}mm p_h={p_h}mm step1_img={step1_img}"
                        )

                        if room_w > 0 and room_d > 0 and room_h > 0 and p_w > 0 and p_d > 0 and p_h > 0 and step1_img:
                            ratios = {"w": p_w / room_w, "d": p_d / room_d, "h": p_h / room_h}
                            guide_out = os.path.join("outputs", f"scale_guide_{unique_id}.png")
                            scale_guide_path = create_scale_guide_image(
                                step1_img,
                                wall_span_norm,
                                ratios["w"],
                                guide_out,
                                room_planes=room_planes if room_planes else None,
                                target_ratios=ratios if room_planes else None,
                            )

                            if scale_guide_path and os.path.exists(scale_guide_path):
                                suffix = " (fallback W-only)" if not room_planes else ""
                                logger.info(
                                    f"[Scale] scale guide saved: {scale_guide_path} "
                                    f"(W={ratios['w']:.4f}, D={ratios['d']:.4f}, H={ratios['h']:.4f}, anchor={scale_anchor_label})"
                                    f"{suffix}"
                                )
                            else:
                                logger.warning(f"[Scale] scale guide create returned: {scale_guide_path}")
                        else:
                            logger.warning("[Scale] Skipped W/D/H scale guide: missing room dims, item dims, or image")
                            try:
                                summary["scale_guide_skipped"] = summary.get("scale_guide_skipped", 0) + 1
                            except Exception:
                                pass
                    except Exception as e:
                        logger.exception(f"[Scale] scale guide exception: {e}")

                except Exception as e:
                    logger.exception(f"[Scale] furniture JSON build failed: {e}")
                    furniture_specs_json = None
                    primary_item = None
                    scale_guide_path = None
                    size_hierarchy = None
                
                print(f">> [Global Analysis] Complete. Specs injected.", flush=True)
                
            except Exception as e:
                print(f"!! [Global Analysis Failed] {e}", flush=True)

        generated_results = []
        log_section("[Stage 2] 3 variations start (Specs Injection)")

        def process_one_variant(index):
            sub_id = f"{unique_id}_v{index+1}"
            try:
                current_style_prompt = STYLES.get(style, "Custom Moodboard Style")
                res = generate_furnished_room(
                    step1_img,
                    current_style_prompt,
                    ref_path,
                    sub_id,
                    furniture_specs=furniture_specs_text,
                    furniture_specs_json=furniture_specs_json,
                    room_dimensions=dimensions,
                    placement_instructions=placement,
                    scale_guide_path=scale_guide_path,
                    primary_item=primary_item,
                    room_dims_parsed=room_dims_parsed,
                    wall_span_norm=wall_span_norm,
                    size_hierarchy=size_hierarchy,
                    start_time=start_time,
                    room_planes=room_planes,
                )
                if res: return f"/outputs/{os.path.basename(res)}"
            except Exception as e: print(f"   ❌ [Variation {index+1}] 에러: {e}", flush=True)
            return None

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(process_one_variant, i) for i in range(3)]
            for future in futures:
                res = future.result()
                if res: generated_results.append(res)
                gc.collect()

        if LOG_SUMMARY:
            reasons = []
            if summary.get('dims_fail',0):
                reasons.append(f"Dims fail={summary.get('dims_fail',0)}")
            if summary.get('dims_warn',0):
                reasons.append(f"Dims warn={summary.get('dims_warn',0)}")
            if summary.get('scalecheck_fail',0):
                reasons.append(f"ScaleCheck fail={summary.get('scalecheck_fail',0)}")
            if summary.get('scale_guide_skipped',0):
                reasons.append(f"Scale guide skipped={summary.get('scale_guide_skipped',0)}")
            ok = summary.get('text_ok',0)
            fail = summary.get('text_fail',0)
            if reasons:
                logger.warning("WARNING: %s | TextRead OK=%s FAIL=%s", '; '.join(reasons), ok, fail)
            else:
                logger.info("OK: TextRead OK=%s FAIL=%s", ok, fail)
        final_before_url = f"/outputs/{os.path.basename(step1_img)}"
        if not generated_results: generated_results.append(f"/outputs/{os.path.basename(step1_img)}")

        scale_guide_url = None
        try:
            if scale_guide_path and os.path.exists(scale_guide_path):
                scale_guide_url = f"/outputs/{os.path.basename(scale_guide_path)}"
        except Exception:
            pass

        if summary_token is not None:
            SUMMARY_REF.reset(summary_token)
        return JSONResponse(content={
            "original_url": f"/outputs/{os.path.basename(std_path)}",
            "empty_room_url": final_before_url,
            "result_url": generated_results[0],
            "result_urls": generated_results,
            "moodboard_url": mb_url,
            "scale_guide_url": scale_guide_url,   # ✅ 추가
            "furniture_data": full_analyzed_data,
            "message": "Complete"
        })

    except Exception as e:
        if summary_token is not None:
            try:
                SUMMARY_REF.reset(summary_token)
            except Exception:
                pass
        print(f"\n🔥🔥🔥 [SERVER CRASH] {e}", flush=True)
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

class UpscaleRequest(BaseModel): image_url: str

class FinalizeRequest(BaseModel):
    image_url: str

@app.post("/finalize-download")
@async_wrap
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
@async_wrap
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
    img = None
    try:
        img = Image.open(original_image_path)
        target_ratio = style_config.get('ratio', '16:9')
        CROP_LOCK_BLOCK = (
            "<ABSOLUTE RULE #0 — THIS IS THE SAME PHOTO>\n"
            "This output MUST be a CROPPED/REFRAMED photograph of the EXACT SAME furnished room image provided.\n"
            "You are NOT creating a new image. You are NOT restaging. You are NOT redesigning.\n"
            "Allowed operations: camera framing, crop, zoom, slight depth-of-field.\n"
            "Forbidden operations: moving/adding/removing/replacing ANY object, changing materials, changing colors, changing lighting style.\n"
            "Every pixel that is not affected by the crop/zoom MUST remain visually consistent with the input.\n"
        )

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
                    try:
                        if img:
                            img.close()
                    except Exception:
                        pass
                    return f"/outputs/{filename}"
        try:
            if img:
                img.close()
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"!! Detail Generation Error: {e}")
        try:
            if img:
                img.close()
        except Exception:
            pass
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
@async_wrap
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
        
        if not dynamic_styles:
            return JSONResponse(content={"error": "No styles available"}, status_code=400)

        idx = req.style_index
        if idx < 0:
            idx = 0
        elif idx >= len(dynamic_styles):
            idx = len(dynamic_styles) - 1

        unique_id = uuid.uuid4().hex[:6]
        style = dynamic_styles[idx]
        
        res = generate_detail_view(local_path, style, unique_id, idx + 1)
        
        if res:
            return JSONResponse(content={"url": res, "message": "Success"})
        else:
            return JSONResponse(content={"error": "Generation failed"}, status_code=500)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# [수정] main.py 내부의 generate_details_endpoint 함수 교체

@app.post("/generate-details")
@async_wrap
def generate_details_endpoint(req: DetailRequest):
    try:
        # 1. 대상 이미지 경로 확보
        filename = os.path.basename(req.image_url)
        local_path = os.path.join("outputs", filename)
        if not os.path.exists(local_path):
            return JSONResponse(content={"error": "Original image not found"}, status_code=404)

        unique_id = uuid.uuid4().hex[:6]
        log_section(f"[Detail View] REQUEST START ({unique_id}) - Smart Analysis Mode")

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
    img = None
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
                    try:
                        if img:
                            img.close()
                    except Exception:
                        pass
                    return f"/outputs/{filename}"
        try:
            if img:
                img.close()
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"!! Moodboard Gen Error: {e}")
        try:
            if img:
                img.close()
        except Exception:
            pass
        return None

@app.post("/generate-moodboard-options")
@async_wrap
def generate_moodboard_options(file: UploadFile = File(...)):
    try:
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._-"])
        raw_path = os.path.join("outputs", f"ref_room_{timestamp}_{unique_id}_{safe_name}")
        
        with open(raw_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
        
        log_section(f"[Moodboard Gen] Starting 3 variations for {unique_id}")

        furniture_specs_text = None
        try:
            print(">> [Moodboard Gen] Analyzing input photo context...", flush=True)
            detected = detect_furniture_boxes(raw_path)
            specs_list = [f"- {item['label']}" for item in detected]
            furniture_specs_text = "\n".join(specs_list)
        except:
            print("!! [Moodboard Gen] Context analysis failed (skipping)")
        
        generated_results = []
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(generate_moodboard_logic, raw_path, unique_id, i+1, furniture_specs_text) for i in range(3)]
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
@async_wrap
async def api_generate_sources(req: SourceGenRequest):
    job_id = uuid.uuid4().hex
    with video_jobs_lock:
        video_jobs[job_id] = {"status": "QUEUED", "progress": 0}
    
    # 백그라운드 스레드로 실행
    threading.Thread(target=_run_source_generation, args=(job_id, req.items, req.cfg_scale)).start()
    return {"job_id": job_id}

@app.post("/video-mvp/compile")
@async_wrap
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
    reload_flag = os.getenv("DEV_RELOAD", "0") == "1"
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=reload_flag, log_level="info")
