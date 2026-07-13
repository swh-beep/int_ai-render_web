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
import boto3
import mimetypes
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from application.details.detail_generation_stage import generate_detail_view as generate_detail_view_stage
from application.details.detail_style_stage import construct_dynamic_styles as construct_dynamic_styles_stage
from application import job_entrypoints as job_entrypoints_module
from application.job_entrypoints import JobEntrypointServices
from application.http.queue_route_handlers import (
    QueueRouteDependencies,
    handle_api_external_render_cart,
    handle_api_external_render_cart_simple,
    handle_api_external_render_cart_simple_batch,
    handle_api_external_render_preset,
    handle_api_external_render_video,
    handle_api_internal_render,
    handle_finalize_async,
    handle_generate_details,
    handle_generate_empty_room_async,
    handle_generate_frontal_view_async,
    handle_generate_image_edit_async,
    handle_get_job_status,
    handle_regenerate_single_detail,
    handle_render_room_async,
    handle_upscale_async,
)
from application.http.local_job_store import enqueue_local_job, get_local_job
from application.http.staging_job_store import (
    get_staging_job_state,
    set_staging_job_state,
    update_staging_job_state,
)
from application.media.frontal_generation_stage import (
    generate_frontal_room_from_photos as generate_frontal_room_from_photos_stage,
)
from application.media.image_edit_generation_stage import (
    process_image_edit_logic as process_image_edit_logic_stage,
)
from application.render.empty_room_generation_stage import generate_empty_room as generate_empty_room_stage
from application.render.furnished_generation_stage import generate_furnished_room as generate_furnished_room_stage
from application.render.dimension_support import (
    available_dim_axes as available_dim_axes_support,
    dims_has_positive_values as dims_has_positive_values_support,
    dims_to_str as dims_to_str_support,
    is_rug_like as is_rug_like_support,
    is_two_dim_ok_label as is_two_dim_ok_label_support,
    normalize_dims_dict as normalize_dims_dict_support,
    parse_object_dimensions_mm as parse_object_dimensions_mm_support,
    parse_room_dimensions_mm as parse_room_dimensions_mm_support,
)
from application.render.furniture_specs_stage import (
    attach_volume_ranks as attach_volume_ranks_stage,
    build_furniture_specs_json as build_furniture_specs_json_stage,
    item_box_area_proxy as item_box_area_proxy_stage,
    volume_proxy as volume_proxy_stage,
    volume_ranking_snapshot as volume_ranking_snapshot_stage,
)
from application.render.item_analysis_stage import (
    _crop_item_with_padding as crop_item_with_padding_stage,
    analyze_cropped_item as analyze_cropped_item_stage,
    detect_furniture_boxes as detect_furniture_boxes_stage,
)
from application.render.postprocess_support import (
    build_item_target_key as build_item_target_key_support,
    canonical_category as canonical_category_support,
    label_match_score as label_match_score_support,
    normalize_label_for_match as normalize_label_for_match_support,
    rank_best_variant_flash as rank_best_variant_flash_support,
    remap_match_score as remap_match_score_support,
    refresh_item_boxes_from_main_render as refresh_item_boxes_from_main_render_support,
    safe_key_token as safe_key_token_support,
    summarize_items_for_ranking as summarize_items_for_ranking_support,
)
from application.render.moodboard_workflow import run_generate_moodboard_options
from application.render.render_contracts import (
    build_explicit_room_dims_contract as build_explicit_room_dims_contract_stage,
)
from application.render.direct_item_image_prep import prepare_direct_item_image
from application.render.render_room_workflow import run_render_room_workflow
from application.render.render_workflow_contracts import (
    RenderWorkflowAnalysisServices,
    RenderWorkflowDependencies,
    RenderWorkflowGenerationServices,
    RenderWorkflowPostprocessServices,
    RenderWorkflowRequest,
    RenderWorkflowRuntime,
    RenderWorkflowStorageServices,
)
from application.render.room_dimension_estimation_stage import (
    estimate_room_dims_contract as estimate_room_dims_contract_stage,
)
from application.render.room_analysis import analyze_room_and_items_long as analyze_room_and_items_long_stage
from application.render.room_analysis import analyze_room_structure as analyze_room_structure_stage
from application.render.scene_contract_stage import build_scene_contract as build_scene_contract_stage
from application.render.geometry_contract_stage import build_geometry_contract as build_geometry_contract_stage
from application.render.product_identity_stage import (
    build_product_identity_bundle as build_product_identity_bundle_stage,
)
from application.render.archetype_strategy_stage import (
    build_archetype_strategies as build_archetype_strategies_stage,
)
from application.render.placement_plan_stage import (
    build_placement_plan as build_placement_plan_stage,
)
from application.render.scale_validation_support import (
    crop_ref_item_image as crop_ref_item_image_support,
    detect_back_wall_span_norm as detect_back_wall_span_norm_support,
    detect_item_bbox_norm as detect_item_bbox_norm_support,
    detect_primary_bbox_norm as detect_primary_bbox_norm_support,
    detect_windows_present as detect_windows_present_support,
    reorder_by_scale_best_pick as reorder_by_scale_best_pick_support,
    score_scale as score_scale_support,
    validate_furnished_scale as validate_furnished_scale_support,
)
from application.render.scale_guide_support import (
    create_scale_guide_overlay_with_model as create_scale_guide_overlay_with_model_support,
    room_dims_valid as room_dims_valid_support,
)
from application.video.compile_workflow import queue_final_compile_job, run_final_compile_job
from application.video.job_store import get_video_job, prune_video_jobs, update_video_job
from application.video.queueing import (
    build_video_status_payload,
    enqueue_compile_rq_job,
    enqueue_source_generation_rq_job,
    publish_video_state_outputs,
)
from application.video.source_generation_workflow import queue_source_generation_job, run_source_generation_job
from application.video.video_support import download_to_path as _download_to_path
from dotenv import load_dotenv
from infrastructure.ai.analysis_provider_dispatch import (
    build_analysis_model_set,
    build_analysis_provider_dispatch,
)
from infrastructure.ai.image_provider_dispatch import build_image_provider_dispatch
from infrastructure.ai.gemini_client import call_gemini_with_failover as call_gemini_with_failover_impl
from infrastructure.ai.openai_analysis_client import call_openai_analysis as call_openai_analysis_impl
from infrastructure.ai.openai_image_client import call_openai_image as call_openai_image_impl
from infrastructure.ai.provider_defaults import (
    resolve_provider_defaults,
    resolve_runtime_image_provider,
    resolve_runtime_model_name,
)
from infrastructure.ai.freepik_kling_client import (
    build_kling_endpoint,
    create_kling_task as create_kling_task_impl,
    poll_kling_task as poll_kling_task_impl,
)
from infrastructure.ai.gemini_policy import allow_all_safety_settings, allow_harassment_only_safety_settings
from infrastructure.ai.gemini_prompts import (
    build_empty_room_prompt,
    build_frontal_analysis_prompt,
    build_frontal_generation_prompt,
    build_image_edit_step_prompt,
    build_moodboard_generation_prompt,
)
from infrastructure.ai.magnific_client import call_magnific_api as call_magnific_api_impl
from styles_config import STYLES, ROOM_STYLES
from PIL import Image, ImageOps
import re
import traceback
import sys
import logging
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc
from typing import Optional, List, Dict, Any
from contextvars import ContextVar
from redis import Redis
from rq import Queue, Retry, get_current_job
from rq.job import Job
from api_models import (
    CartItem,
    CartRenderRequest,
    CartSimpleBatchRequest,
    CompileClip,
    CompileRequest,
    DetailRequest,
    ExternalRenderVideoRequest,
    FinalizeRequest,
    InternalRenderRequest,
    PresetRenderRequest,
    RegenerateDetailRequest,
    SourceGenRequest,
    SourceItem,
    UpscaleRequest,
    VideoClip,
    VideoCreateRequest,
)
from preset_helpers import load_preset_map
from application.http.internal_render_form_parser import parse_internal_render_items_form
from render_route_services import (
    build_detail_generation_job_payload,
    build_empty_room_job_payload,
    build_external_cart_batch_job,
    build_external_cart_job,
    build_external_preset_job,
    build_external_render_video_job,
    build_finalize_download_job_payload,
    build_frontal_view_job_payload,
    build_image_edit_job_payload,
    build_internal_render_job_payload,
    build_internal_itemized_async_render_job_payload,
    prepare_internal_item_upload_paths,
    build_regenerate_detail_job_payload,
    build_upscale_job_payload,
    persist_internal_media_uploads,
    persist_internal_item_uploads,
    persist_internal_item_source_uploads,
    persist_internal_room_upload,
)
from request_helpers import apply_cart_limits, build_cart_summary, require_role
from storage_helpers import (
    find_s3_moodboard_key,
    is_allowed_download_url,
    load_job_result_s3,
    normalize_s3_prefix,
    publish_image as publish_image_impl,
    resolve_image_url as resolve_image_url_impl,
    s3_enabled,
    s3_list_keys,
    s3_prefix_from_url,
    s3_public_url,
    save_job_result_s3,
)
from shared.image_canvas import (
    match_aspect_to_target as match_aspect_to_target_shared,
    pad_image_to_target_canvas as pad_image_to_target_canvas_shared,
    set_png_dpi as set_png_dpi_shared,
    standardize_image as standardize_image_shared,
    standardize_image_to_reference_canvas as standardize_image_to_reference_canvas_shared,
    standardize_image_to_target_canvas as standardize_image_to_target_canvas_shared,
)

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
OUTPUTS_DIR = BASE_DIR / "outputs"
ASSETS_DIR = BASE_DIR / "assets"
STATIC_DIR = BASE_DIR / "static"

# ---------------------------------------------------------
# 1. 환경 설정 및 초기화
# ---------------------------------------------------------
load_dotenv(BASE_DIR / ".env")
LOG_BRIEF = os.getenv("LOG_BRIEF", "1") == "1"
LOG_SUMMARY = os.getenv("LOG_SUMMARY", "1") == "1"
SCALE_CHECK = False  # deprecated: scale-check disabled by policy
SCALE_GUIDE_GRID_ONLY = os.getenv("SCALE_GUIDE_GRID_ONLY", "1") == "1"
SUMMARY_REF = ContextVar("SUMMARY_REF", default=None)


def _reset_summary_token(summary_token) -> None:
    if summary_token is None:
        return
    try:
        SUMMARY_REF.reset(summary_token)
    except Exception:
        pass

def _calc_app_build_id() -> str:
    env_val = os.getenv("APP_BUILD_ID", "").strip()
    if env_val:
        return env_val
    candidates = []
    try:
        candidates.append(os.path.getmtime(__file__))
    except Exception:
        pass
    try:
        for root, _, files in os.walk(STATIC_DIR):
            for fname in files:
                if fname.lower().endswith((".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".json", ".svg")):
                    try:
                        candidates.append(os.path.getmtime(os.path.join(root, fname)))
                    except Exception:
                        pass
    except Exception:
        pass
    ts = max(candidates) if candidates else time.time()
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))

APP_BUILD_ID = _calc_app_build_id()
GEMINI_MAX_CONCURRENCY_ANALYSIS = 30

MODEL_NAME = 'gemini-3-pro-image'
DEFAULT_GEMINI_MAIN_IMAGE_MODEL_NAME = "gemini-3-pro-image"


def _default_direct_gemini_image_model_name() -> str:
    configured_model = (
        os.getenv("GEMINI_IMAGE_MODEL_NAME")
        or os.getenv("MAIN_IMAGE_MODEL_NAME")
        or ""
    ).strip()
    normalized = configured_model.lower()
    if configured_model and not normalized.startswith(("gpt-", "dall-e-", "chatgpt-")):
        return configured_model
    return DEFAULT_GEMINI_MAIN_IMAGE_MODEL_NAME


GEMINI_IMAGE_MODEL_NAME = _default_direct_gemini_image_model_name()
PROVIDER_DEFAULTS = resolve_provider_defaults(os.environ)
FORCE_GEMINI_ANALYSIS_PROVIDER = PROVIDER_DEFAULTS.force_gemini_analysis_provider
FORCE_GEMINI_IMAGE_PROVIDERS = PROVIDER_DEFAULTS.force_gemini_image_providers
CONFIGURED_ANALYSIS_PROVIDER = os.getenv("ANALYSIS_PROVIDER", "gemini").strip().lower() or "gemini"
OPENAI_ANALYSIS_MODEL_NAME = os.getenv("OPENAI_ANALYSIS_MODEL_NAME", "gpt-5.4").strip() or "gpt-5.4"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_IMAGE_VERIFICATION_FALLBACK_MODEL_NAME = os.getenv("OPENAI_IMAGE_VERIFICATION_FALLBACK_MODEL_NAME", "").strip()
CONFIGURED_MAIN_IMAGE_PROVIDER = os.getenv("MAIN_IMAGE_PROVIDER", "gemini").strip().lower() or "gemini"
CONFIGURED_REPAIR_IMAGE_PROVIDER = os.getenv("REPAIR_IMAGE_PROVIDER", "gemini").strip().lower() or "gemini"
ANALYSIS_PROVIDER = PROVIDER_DEFAULTS.analysis_provider
MAIN_IMAGE_PROVIDER = resolve_runtime_image_provider(PROVIDER_DEFAULTS.main_image_provider, OPENAI_API_KEY)
REPAIR_IMAGE_PROVIDER = resolve_runtime_image_provider(PROVIDER_DEFAULTS.repair_image_provider, OPENAI_API_KEY)
OPENAI_IMAGE_MODEL_NAME = PROVIDER_DEFAULTS.openai_image_model_name


def _resolve_openai_analysis_reasoning_effort(raw_value: str | None) -> str:
    normalized = str(raw_value or "").strip().lower()
    if normalized == "high":
        return "high"
    return "xhigh"


OPENAI_ANALYSIS_REASONING_EFFORT = _resolve_openai_analysis_reasoning_effort(
    os.getenv("OPENAI_ANALYSIS_REASONING_EFFORT", "xhigh")
)


def _default_main_image_model_name() -> str:
    return resolve_runtime_model_name(
        provider=MAIN_IMAGE_PROVIDER,
        configured_model_name=os.getenv("MAIN_IMAGE_MODEL_NAME"),
        default_openai_model_name=OPENAI_IMAGE_MODEL_NAME,
        default_gemini_model_name=GEMINI_IMAGE_MODEL_NAME,
    )


def _default_repair_image_model_name() -> str:
    return resolve_runtime_model_name(
        provider=REPAIR_IMAGE_PROVIDER,
        configured_model_name=os.getenv("REPAIR_IMAGE_MODEL_NAME"),
        default_openai_model_name=OPENAI_IMAGE_MODEL_NAME,
        default_gemini_model_name=GEMINI_IMAGE_MODEL_NAME,
    )


MAIN_IMAGE_MODEL_NAME = _default_main_image_model_name()
REPAIR_IMAGE_MODEL_NAME = _default_repair_image_model_name()


def _default_analysis_model_name(configured_model_name: str | None) -> str:
    return resolve_runtime_model_name(
        provider=ANALYSIS_PROVIDER,
        configured_model_name=configured_model_name,
        default_openai_model_name=OPENAI_ANALYSIS_MODEL_NAME,
        default_gemini_model_name="gemini-3.5-flash",
    )


ANALYSIS_MODEL_NAME = _default_analysis_model_name(os.getenv("ANALYSIS_MODEL_NAME"))
DETECT_FURNITURE_MODEL_NAME = _default_analysis_model_name(os.getenv("DETECT_FURNITURE_MODEL_NAME"))
ROOM_ONLY_MODEL_NAME = _default_analysis_model_name(os.getenv("ROOM_ONLY_MODEL_NAME"))
RANK_MODEL_NAME = _default_analysis_model_name(os.getenv("RANK_MODEL_NAME"))
REMAP_MODEL_NAME = _default_analysis_model_name(os.getenv("REMAP_MODEL_NAME"))
REMAP_DETECT_TIMEOUT_SEC = max(10, int(os.getenv("REMAP_DETECT_TIMEOUT_SEC", "60")))
REMAP_DETECT_RETRY = max(0, int(os.getenv("REMAP_DETECT_RETRY", "1")))
CART_MAX_ITEMS = max(1, int(os.getenv("CART_MAX_ITEMS", "20")))
CART_MAX_ANALYSIS_WORKERS = max(1, int(os.getenv("CART_MAX_ANALYSIS_WORKERS", "10")))
OPENAI_ANALYSIS_MODEL_SET = build_analysis_model_set(
    ANALYSIS_MODEL_NAME,
    DETECT_FURNITURE_MODEL_NAME,
    ROOM_ONLY_MODEL_NAME,
    RANK_MODEL_NAME,
    REMAP_MODEL_NAME,
)

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
TOTAL_TIMEOUT_LIMIT = max(60, int(os.getenv("TOTAL_TIMEOUT_LIMIT", "1800")))
REDIS_URL = os.getenv("REDIS_URL", "").strip()
LOCAL_INLINE_QUEUE_ENABLED = os.getenv("LOCAL_INLINE_QUEUE", "0").strip().lower() in ("1", "true", "yes", "y")

def _split_queue_names(val: str) -> List[str]:
    return [p.strip() for p in val.replace(";", ",").split(",") if p.strip()]

_rq_name_raw = os.getenv("RQ_QUEUE_NAME", "").strip()
_rq_render_raw = os.getenv("RQ_QUEUE_RENDER", "").strip()
_rq_upscale_raw = os.getenv("RQ_QUEUE_UPSCALE", "").strip()
_rq_video_raw = os.getenv("RQ_QUEUE_VIDEO", "").strip()

_rq_name_parts = _split_queue_names(_rq_name_raw) if _rq_name_raw else []
RQ_QUEUE_NAME = (_rq_name_parts[0] if _rq_name_parts else (_rq_name_raw or "default")).strip() or "default"

# If render/upscale not explicitly set, try to derive from RQ_QUEUE_NAME list.
if not _rq_render_raw and _rq_name_parts:
    _rq_render_raw = _rq_name_parts[0]
if not _rq_upscale_raw and len(_rq_name_parts) >= 2:
    _rq_upscale_raw = _rq_name_parts[1]
if not _rq_video_raw and len(_rq_name_parts) >= 3:
    _rq_video_raw = _rq_name_parts[2]

RQ_QUEUE_RENDER = (_rq_render_raw or RQ_QUEUE_NAME).strip() or RQ_QUEUE_NAME
RQ_QUEUE_UPSCALE = (_rq_upscale_raw or RQ_QUEUE_NAME).strip() or RQ_QUEUE_NAME
RQ_QUEUE_VIDEO = (_rq_video_raw or RQ_QUEUE_UPSCALE).strip() or RQ_QUEUE_UPSCALE
RQ_JOB_TIMEOUT = int(os.getenv("RQ_JOB_TIMEOUT", "1800"))
RQ_VIDEO_JOB_TIMEOUT = int(os.getenv("RQ_VIDEO_JOB_TIMEOUT", "3600"))
RQ_RESULT_TTL = int(os.getenv("RQ_RESULT_TTL", "604800"))
STAGING_JOB_TTL = int(os.getenv("STAGING_JOB_TTL", "86400"))
RQ_RETRY_MAX = int(os.getenv("RQ_RETRY_MAX", "2"))
RQ_RETRY_INTERVALS = os.getenv("RQ_RETRY_INTERVALS", "30,90").strip()
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
AWS_REGION = os.getenv("AWS_REGION", "").strip()
S3_PREFIX = os.getenv("S3_PREFIX", "").strip()
S3_REQUIRED = os.getenv("S3_REQUIRED", "0").strip().lower() in ("1", "true", "yes", "y")
JOB_RESULT_S3_PREFIX = os.getenv("JOB_RESULT_S3_PREFIX", "").strip()
DEFAULT_AUDIENCE = os.getenv("DEFAULT_AUDIENCE", "internal").strip().lower() or "internal"
MOODBOARD_S3_PREFIX = os.getenv("MOODBOARD_S3_PREFIX", "moodboard/").strip()
USE_S3_MOODBOARD = os.getenv("USE_S3_MOODBOARD", "0").strip().lower() in ("1", "true", "yes", "y")
API_AUTH_DISABLED = os.getenv("API_AUTH_DISABLED", "0").strip().lower() in ("1", "true", "yes", "y")
INTERNAL_INTEA_API_KEYS = set()
EXTERNAL_INTEA_API_KEYS = set()
PRESET_MAP_PATH = os.getenv("PRESET_MAP_PATH", "").strip()
CART_LIMITS_JSON = os.getenv("CART_LIMITS_JSON", "").strip()
_PUBLISHED_URL_CACHE = {}

def _normalize_audience(audience: Optional[str]) -> str:
    aud = (audience or DEFAULT_AUDIENCE or "internal").strip().lower()
    if aud in ("external", "public", "customer", "client"):
        return "external"
    return "internal"

def _build_s3_prefix(audience: Optional[str], category: Optional[str] = None, subfolder: Optional[str] = None) -> str:
    base = _normalize_s3_prefix(S3_PREFIX)
    parts = []
    if category == "moodboard":
        parts = [MOODBOARD_S3_PREFIX.strip("/")]
    elif category:
        parts = [_normalize_audience(audience), category]
        if subfolder:
            parts.append(subfolder)
    if parts:
        return base + "/".join([p for p in parts if p]) + "/"
    return base

def _s3_prefix_from_url(url: str) -> Optional[str]:
    return s3_prefix_from_url(url, S3_BUCKET)


def _parse_key_list(value: str) -> set:
    if not value:
        return set()
    parts = [p.strip() for p in value.replace(";", ",").split(",")]
    return {p for p in parts if p}

INTERNAL_INTEA_API_KEYS = _parse_key_list(os.getenv("INTERNAL_INTEA_API_KEYS", ""))
EXTERNAL_INTEA_API_KEYS = _parse_key_list(os.getenv("EXTERNAL_INTEA_API_KEYS", ""))
DOWNLOAD_ALLOWED_HOSTS = _parse_key_list(os.getenv("DOWNLOAD_ALLOWED_HOSTS", ""))
DOWNLOAD_ALLOW_PUBLIC_CLOUD_HOSTS = os.getenv("DOWNLOAD_ALLOW_PUBLIC_CLOUD_HOSTS", "0").strip().lower() in ("1", "true", "yes", "y")
OUTPUTS_API_ROLE = os.getenv("OUTPUTS_API_ROLE", "").strip().lower()
OUTPUTS_API_ENABLED = os.getenv("OUTPUTS_API_ENABLED", "1").strip().lower() in ("1", "true", "yes", "y")
OUTPUTS_UPLOAD_MAX_MB = max(1, int(os.getenv("OUTPUTS_UPLOAD_MAX_MB", "25") or "25"))
OUTPUTS_UPLOAD_MAX_BYTES = OUTPUTS_UPLOAD_MAX_MB * 1024 * 1024
OUTPUTS_ALLOWED_EXTS = {
    ext.strip().lower()
    for ext in os.getenv("OUTPUTS_ALLOWED_EXTS", ".png,.jpg,.jpeg,.webp").replace(";", ",").split(",")
    if ext.strip()
}
OUTPUTS_VIDEO_UPLOAD_MAX_MB = max(1, int(os.getenv("OUTPUTS_VIDEO_UPLOAD_MAX_MB", "100") or "100"))
OUTPUTS_VIDEO_UPLOAD_MAX_BYTES = OUTPUTS_VIDEO_UPLOAD_MAX_MB * 1024 * 1024
OUTPUTS_VIDEO_ALLOWED_EXTS = {
    ext.strip().lower()
    for ext in os.getenv("OUTPUTS_VIDEO_ALLOWED_EXTS", ".mp4,.mov,.webm").replace(";", ",").split(",")
    if ext.strip()
}
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").replace(";", ",").split(",")
    if origin.strip()
] or ["*"]
CORS_ALLOW_CREDENTIALS = os.getenv(
    "CORS_ALLOW_CREDENTIALS",
    "0" if "*" in CORS_ALLOW_ORIGINS else "1",
).strip().lower() in ("1", "true", "yes", "y")

PRESET_MAP_CACHE = None

DEFAULT_CART_LIMITS = {
    "sofa": 2,
    "sectional": 1,
    "lounge_chair": 4,
    "chair": 6,
    "dining_chair": 8,
    "table": 2,
    "dining_table": 1,
    "bed": 2,
    "rug": 2,
    "lamp": 6,
    "floor_lamp": 4,
    "table_lamp": 6,
    "decor": 20,
}

try:
    CART_LIMITS = json.loads(CART_LIMITS_JSON) if CART_LIMITS_JSON else DEFAULT_CART_LIMITS
except Exception:
    CART_LIMITS = DEFAULT_CART_LIMITS


def _s3_enabled() -> bool:
    return s3_enabled(S3_BUCKET, AWS_REGION)

def _get_s3_client():
    return boto3.client("s3", region_name=AWS_REGION or None)

def _normalize_s3_prefix(prefix: str) -> str:
    return normalize_s3_prefix(prefix)

def _s3_public_url(key: str) -> str:
    return s3_public_url(S3_BUCKET, AWS_REGION, key)

def _job_result_prefix(audience: Optional[str] = None) -> str:
    base = _normalize_s3_prefix(S3_PREFIX)
    if JOB_RESULT_S3_PREFIX:
        return f"{base}{_normalize_s3_prefix(JOB_RESULT_S3_PREFIX)}"
    if audience:
        return _build_s3_prefix(audience, "job-results")
    return f"{base}job-results/"

def _job_result_key(job_id: str, audience: Optional[str] = None) -> Optional[str]:
    if not job_id:
        return None
    prefix = _job_result_prefix(audience)
    return f"{prefix}{job_id}.json"

def _job_result_key_candidates(job_id: str) -> list[str]:
    if not job_id:
        return []
    base = _normalize_s3_prefix(S3_PREFIX)
    candidates: list[str] = []
    if JOB_RESULT_S3_PREFIX:
        candidates.append(f"{base}{_normalize_s3_prefix(JOB_RESULT_S3_PREFIX)}{job_id}.json")
    else:
        candidates.append(f"{_build_s3_prefix('external', 'job-results')}{job_id}.json")
        candidates.append(f"{_build_s3_prefix('internal', 'job-results')}{job_id}.json")
        candidates.append(f"{base}job-results/{job_id}.json")
    # De-duplicate while preserving order
    seen = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]

def _save_job_result_s3(job_id: str, result: dict, audience: Optional[str] = None) -> Optional[str]:
    return save_job_result_s3(
        job_id,
        result,
        audience,
        S3_BUCKET,
        AWS_REGION,
        S3_PREFIX,
        JOB_RESULT_S3_PREFIX,
        _build_s3_prefix,
        _get_s3_client,
    )

def _load_job_result_s3(job_id: str) -> Optional[dict]:
    return load_job_result_s3(
        job_id,
        S3_BUCKET,
        AWS_REGION,
        S3_PREFIX,
        JOB_RESULT_S3_PREFIX,
        _build_s3_prefix,
        _get_s3_client,
    )

def _s3_list_keys(prefix: str, max_keys: int = 1000) -> list[str]:
    return s3_list_keys(prefix, S3_BUCKET, AWS_REGION, _get_s3_client, max_keys=max_keys)

def _find_s3_moodboard_key(safe_room: str, safe_style: str, variant: str) -> Optional[str]:
    return find_s3_moodboard_key(safe_room, safe_style, variant, _build_s3_prefix, _s3_list_keys)

def publish_image(local_path: Optional[str], s3_prefix_override: Optional[str] = None) -> Optional[str]:
    return publish_image_impl(
        local_path,
        s3_prefix_override,
        S3_PREFIX,
        S3_BUCKET,
        AWS_REGION,
        _PUBLISHED_URL_CACHE,
        _get_s3_client,
    )

def resolve_image_url(local_path: Optional[str], s3_prefix_override: Optional[str] = None) -> Optional[str]:
    return resolve_image_url_impl(
        local_path,
        s3_prefix_override,
        S3_PREFIX,
        S3_BUCKET,
        AWS_REGION,
        S3_REQUIRED,
        _PUBLISHED_URL_CACHE,
        _get_s3_client,
    )

def _require_outputs_api_access(request: Request) -> Optional[JSONResponse]:
    if not OUTPUTS_API_ENABLED:
        return JSONResponse(content={"error": "Outputs API disabled"}, status_code=403)
    if OUTPUTS_API_ROLE in {"internal", "external"}:
        require_role(
            request,
            {OUTPUTS_API_ROLE},
            API_AUTH_DISABLED,
            INTERNAL_INTEA_API_KEYS,
            EXTERNAL_INTEA_API_KEYS,
        )
    return None


async def _store_output_upload(
    file: UploadFile,
    *,
    default_name: str,
    allowed_exts: set[str],
    max_bytes: int,
    max_mb: int,
) -> JSONResponse | dict:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    orig = (file.filename or default_name).strip()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", orig)
    ext = Path(safe).suffix.lower()
    if ext not in allowed_exts:
        allowed = ", ".join(sorted(allowed_exts))
        return JSONResponse(content={"error": f"Unsupported file type. Allowed: {allowed}"}, status_code=400)

    stamp = int(time.time())
    uid = uuid.uuid4().hex[:8]
    filename = f"upload_{stamp}_{uid}_{safe}"
    out_path = OUTPUTS_DIR / filename
    total_bytes = 0

    try:
        with open(out_path, "wb") as file_obj:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    file_obj.close()
                    try:
                        out_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse(
                        content={"error": f"File too large (max {max_mb}MB)"},
                        status_code=413,
                    )
                file_obj.write(chunk)
    finally:
        await file.close()

    if total_bytes == 0:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse(content={"error": "Empty file"}, status_code=400)

    try:
        public_url = _resolve_video_studio_upload_url(out_path)
    except Exception as exc:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse(
            content={"error": f"Upload saved but could not be published for worker access: {exc}"},
            status_code=500,
        )

    return {"filename": filename, "url": public_url, "local_url": f"/outputs/{filename}"}


def _resolve_video_studio_upload_url(path: Path) -> str:
    public_url = resolve_image_url(
        str(path),
        s3_prefix_override=_build_s3_prefix("external", "videorendered", "uploads"),
    )
    if public_url:
        return public_url
    return f"/outputs/{path.name}"


def _resolve_public_file_path(url: str) -> Path | None:
    path_str = (url or "").split("?", 1)[0]
    if path_str.startswith("/outputs/"):
        base_dir = OUTPUTS_DIR
        rel = path_str[len("/outputs/"):]
    elif path_str.startswith("/assets/"):
        base_dir = ASSETS_DIR
        rel = path_str[len("/assets/"):]
    else:
        return None

    candidate = (base_dir / rel).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except Exception:
        return None
    return candidate


def _is_allowed_download_url(url: str, request: Request) -> bool:
    return is_allowed_download_url(
        url,
        request.url.hostname or "",
        S3_BUCKET,
        allowed_hosts=DOWNLOAD_ALLOWED_HOSTS,
        allow_public_cloud_hosts=DOWNLOAD_ALLOW_PUBLIC_CLOUD_HOSTS,
    )

def _get_redis_conn():
    if not REDIS_URL:
        return None
    try:
        return Redis.from_url(REDIS_URL)
    except Exception:
        return None

def _load_preset_map() -> dict:
    global PRESET_MAP_CACHE
    PRESET_MAP_CACHE = load_preset_map(PRESET_MAP_PATH, PRESET_MAP_CACHE)
    return PRESET_MAP_CACHE
def _build_cart_moodboard(items: list[dict], unique_id: str) -> str:
    tile = 512
    cols = min(4, max(1, len(items)))
    rows = (len(items) + cols - 1) // cols
    width = cols * tile
    height = rows * tile
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    for idx, it in enumerate(items):
        img_url = it.get("image_url") or it.get("image")
        if not img_url:
            continue
        local_path = _materialize_input(img_url, f"item_{idx}")
        if not local_path or not os.path.exists(local_path):
            continue
        try:
            with Image.open(local_path) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA"):
                    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg.convert("RGB")
                else:
                    im = im.convert("RGB")
                im.thumbnail((tile, tile), Image.Resampling.LANCZOS)
                x = (idx % cols) * tile + (tile - im.width) // 2
                y = (idx // cols) * tile + (tile - im.height) // 2
                canvas.paste(im, (x, y))
        except Exception:
            continue
    out_path = os.path.join("outputs", f"cart_moodboard_{unique_id}.png")
    canvas.save(out_path)
    return out_path

def _normalize_item_image(local_path: str, unique_id: str, index: int, max_size: int = 1024) -> Optional[str]:
    if not local_path or not os.path.exists(local_path):
        return None
    filename = f"cart_item_{unique_id}_{index}.png"
    out_path = os.path.join("outputs", filename)
    return prepare_direct_item_image(local_path, output_path=out_path, max_size=max_size)

def _get_rq_queue(queue_name: str | None = None):
    conn = _get_redis_conn()
    if not conn:
        return None
    name = (queue_name or RQ_QUEUE_NAME).strip() or RQ_QUEUE_NAME
    return Queue(name, connection=conn, default_timeout=RQ_JOB_TIMEOUT, default_result_ttl=RQ_RESULT_TTL)

def _enqueue_job(func, *args, queue_name: str | None = None, **kwargs):
    q = _get_rq_queue(queue_name)
    retry = None
    job_timeout = kwargs.pop("job_timeout", RQ_JOB_TIMEOUT)
    result_ttl = kwargs.pop("result_ttl", RQ_RESULT_TTL)
    custom_job_id = kwargs.pop("job_id", None)
    if q:
        try:
            if RQ_RETRY_MAX > 0:
                intervals = []
                if RQ_RETRY_INTERVALS:
                    for part in RQ_RETRY_INTERVALS.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        try:
                            intervals.append(int(part))
                        except Exception:
                            continue
                if intervals:
                    retry = Retry(max=RQ_RETRY_MAX, interval=intervals)
                else:
                    retry = Retry(max=RQ_RETRY_MAX)
        except Exception:
            retry = None
        try:
            enqueue_kwargs = {
                "job_timeout": job_timeout,
                "result_ttl": result_ttl,
                "retry": retry,
            }
            if custom_job_id:
                enqueue_kwargs["job_id"] = str(custom_job_id)
            job = q.enqueue(func, *args, **kwargs, **enqueue_kwargs)
            return job, None
        except Exception as exc:
            if not LOCAL_INLINE_QUEUE_ENABLED:
                return None, str(exc)
    if LOCAL_INLINE_QUEUE_ENABLED:
        try:
            return enqueue_local_job(func, *args, job_id=str(custom_job_id) if custom_job_id else None, **kwargs), None
        except Exception as exc:
            return None, str(exc)
    return None, "REDIS_URL not configured"

def _fetch_job(job_id: str):
    if LOCAL_INLINE_QUEUE_ENABLED:
        local_job = get_local_job(job_id)
        if local_job is not None:
            return local_job
    conn = _get_redis_conn()
    if not conn:
        return None
    try:
        return Job.fetch(job_id, connection=conn)
    except Exception:
        return None


def _set_staging_job(job_id: str, state: dict) -> None:
    set_staging_job_state(
        job_id,
        state,
        redis_conn=_get_redis_conn(),
        ttl_sec=STAGING_JOB_TTL,
    )


def _update_staging_job(job_id: str, fields: dict) -> None:
    update_staging_job_state(
        job_id,
        fields,
        redis_conn=_get_redis_conn(),
        ttl_sec=STAGING_JOB_TTL,
    )


def _get_staging_job(job_id: str) -> Optional[dict]:
    return get_staging_job_state(job_id, redis_conn=_get_redis_conn())


def _start_background_task(task):
    thread = threading.Thread(target=task, name=f"staging-job-{uuid.uuid4().hex[:8]}", daemon=True)
    thread.start()

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_CLEANUP_TTL_SEC = max(60 * 60, int(os.getenv("OUTPUT_CLEANUP_TTL_SEC", str(12 * 60 * 60)) or str(12 * 60 * 60)))
OUTPUT_CLEANUP_INTERVAL_SEC = max(300, int(os.getenv("OUTPUT_CLEANUP_INTERVAL_SEC", str(60 * 60)) or str(60 * 60)))
VIDEO_JOB_CACHE_LIMIT = max(100, int(os.getenv("VIDEO_JOB_CACHE_LIMIT", "1000") or "1000"))

def _cleanup_outputs_once():
    now = time.time()
    try:
        for path in OUTPUTS_DIR.iterdir():
            try:
                if not path.is_file():
                    continue
                if now - path.stat().st_mtime > OUTPUT_CLEANUP_TTL_SEC:
                    path.unlink()
            except Exception:
                pass
    except Exception:
        pass

def _cleanup_video_job_cache_once() -> None:
    try:
        prune_video_jobs(VIDEO_JOB_CACHE_LIMIT)
    except Exception:
        pass


def _start_background_cleanup_worker():
    def _worker():
        while True:
            _cleanup_outputs_once()
            _cleanup_video_job_cache_once()
            time.sleep(OUTPUT_CLEANUP_INTERVAL_SEC)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()

_start_background_cleanup_worker()

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

# -----------------------------------------------------------------------------
# RQ async job helpers
# -----------------------------------------------------------------------------
def _materialize_input(path_or_url: str, prefix: str = "input") -> str | None:
    if not path_or_url:
        return None
    if path_or_url.startswith("http://") or path_or_url.startswith("https://") or path_or_url.startswith("/outputs/"):
        base = os.path.basename(path_or_url.split("?")[0])
        if not base:
            base = f"{prefix}_{uuid.uuid4().hex}.bin"
        local_path = os.path.join("outputs", f"{prefix}_{uuid.uuid4().hex[:8]}_{base}")
        try:
            _download_to_path(path_or_url, Path(local_path))
        except Exception:
            return None
        return local_path
    if os.path.exists(path_or_url):
        return path_or_url
    if path_or_url.startswith("/"):
        base = os.path.basename(path_or_url)
        local_path = os.path.join("outputs", f"{prefix}_{uuid.uuid4().hex[:8]}_{base}")
        try:
            _download_to_path(path_or_url, Path(local_path))
        except Exception:
            return None
        return local_path
    return path_or_url

def job_render(payload: dict, persist_result: bool = True) -> dict:
    return job_entrypoints_module.job_render(payload, persist_result=persist_result)

def job_render_with_details(payload: dict) -> dict:
    return job_entrypoints_module.job_render_with_details(payload)

def job_render_with_extra(payload: dict) -> dict:
    return job_entrypoints_module.job_render_with_extra(payload)

def job_render_cart_simple_batch(payload: dict) -> dict:
    return job_entrypoints_module.job_render_cart_simple_batch(payload)

def job_generate_render_video(payload: dict) -> dict:
    return job_entrypoints_module.job_generate_render_video(payload)

def job_image_edit(payload: dict) -> dict:
    return job_entrypoints_module.job_image_edit(payload)

def job_finalize(payload: dict) -> dict:
    return job_entrypoints_module.job_finalize(payload)

def job_generate_empty_room(payload: dict) -> dict:
    return job_entrypoints_module.job_generate_empty_room(payload)

def job_upscale(payload: dict) -> dict:
    return job_entrypoints_module.job_upscale(payload)

def job_frontal_view(payload: dict) -> dict:
    return job_entrypoints_module.job_frontal_view(payload)

def job_generate_details(payload: dict) -> dict:
    return job_entrypoints_module.job_generate_details(payload)

def job_regenerate_single_detail(payload: dict) -> dict:
    return job_entrypoints_module.job_regenerate_single_detail(payload)
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

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

QUOTA_EXCEEDED_KEYS = set()
_analysis_dispatch_logger = logging.getLogger("app")


def _call_gemini_generation(model_name, contents, request_options, safety_settings, system_instruction=None, log_tag=None):
    return call_gemini_with_failover_impl(
        model_name,
        contents,
        request_options,
        safety_settings,
        api_key_pool=API_KEY_POOL,
        quota_exceeded_keys=QUOTA_EXCEEDED_KEYS,
        logger=logger,
        log_brief=LOG_BRIEF,
        system_instruction=system_instruction,
        log_tag=log_tag,
    )


def _call_openai_image_generation(model_name, contents, request_options, safety_settings, system_instruction=None, log_tag=None):
    return call_openai_image_impl(
        model_name,
        contents,
        request_options,
        safety_settings,
        api_key=OPENAI_API_KEY,
        logger=logger,
        log_brief=LOG_BRIEF,
        system_instruction=system_instruction,
        log_tag=log_tag,
        fallback_model_name=OPENAI_IMAGE_VERIFICATION_FALLBACK_MODEL_NAME,
    )


CALL_ANALYSIS_WITH_PROVIDER = build_analysis_provider_dispatch(
    provider=ANALYSIS_PROVIDER,
    gemini_caller=_call_gemini_generation,
    openai_caller=call_openai_analysis_impl,
    openai_model_set=OPENAI_ANALYSIS_MODEL_SET,
    openai_api_key=OPENAI_API_KEY,
    openai_reasoning_effort=OPENAI_ANALYSIS_REASONING_EFFORT,
    logger=_analysis_dispatch_logger,
    log_brief=LOG_BRIEF,
)

CALL_MAIN_IMAGE_WITH_PROVIDER = build_image_provider_dispatch(
    provider=MAIN_IMAGE_PROVIDER,
    gemini_caller=_call_gemini_generation,
    openai_image_caller=_call_openai_image_generation,
    openai_api_key=OPENAI_API_KEY,
)

CALL_REPAIR_IMAGE_WITH_PROVIDER = build_image_provider_dispatch(
    provider=REPAIR_IMAGE_PROVIDER,
    gemini_caller=_call_gemini_generation,
    openai_image_caller=_call_openai_image_generation,
    openai_api_key=OPENAI_API_KEY,
)

def call_gemini_with_failover(model_name, contents, request_options, safety_settings, system_instruction=None, log_tag=None):
    return CALL_ANALYSIS_WITH_PROVIDER(
        model_name,
        contents,
        request_options,
        safety_settings,
        system_instruction=system_instruction,
        log_tag=log_tag,
    )

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
    logging.getLogger("rq.worker").propagate = False
    logging.getLogger("rq.registry").propagate = False
    logging.getLogger("rq.queue").propagate = False

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
    return standardize_image_shared(
        image_path,
        output_path=output_path,
        keep_ratio=keep_ratio,
        force_landscape=force_landscape,
    )

def _set_png_dpi(path: str, dpi: tuple = (300, 300)) -> None:
    return set_png_dpi_shared(path, dpi=dpi)


def standardize_image_to_reference_canvas(
    image_path: str,
    reference_path: str,
    output_path: Optional[str] = None,
) -> str:
    return standardize_image_to_reference_canvas_shared(
        image_path,
        reference_path,
        output_path=output_path,
    )


def standardize_image_to_target_canvas(
    image_path: str,
    target_path: str,
    output_path: Optional[str] = None,
) -> str:
    return standardize_image_to_target_canvas_shared(
        image_path,
        target_path,
        output_path=output_path,
    )


def match_aspect_to_target(
    image_path: str,
    target_path: str,
    output_path: Optional[str] = None,
) -> str:
    return match_aspect_to_target_shared(
        image_path,
        target_path,
        output_path=output_path,
    )
# -----------------------------------------------------------------------------
# [CORE] Analysis Logic (Global Definition)
# -----------------------------------------------------------------------------

# =============================================================================
# [SCALE FIX PACK vB] Robust dimension parsing + furniture spec JSON + auto-pick
# - Keeps existing rendering behavior; only strengthens SCALE guidance & selection.
# - Primary anchor furniture = largest-volume movable furniture EXCLUDING rugs/carpets.
# =============================================================================

def _is_rug_like(label: str) -> bool:
    return is_rug_like_support(label)


def parse_object_dimensions_mm(text: str) -> dict:
    return parse_object_dimensions_mm_support(text)


def parse_room_dimensions_mm(text: str) -> dict:
    return parse_room_dimensions_mm_support(text)

def _volume_proxy(dims: dict) -> int:
    return volume_proxy_stage(dims)


def _item_box_area_proxy(box_2d) -> int:
    return item_box_area_proxy_stage(box_2d)


def _attach_volume_ranks(analyzed_items: list) -> list:
    return attach_volume_ranks_stage(
        analyzed_items,
        normalize_dims_dict=_normalize_dims_dict,
        dims_has_positive_values=_dims_has_positive_values,
        parse_object_dimensions_mm=parse_object_dimensions_mm,
        canonical_category=_canonical_category,
    )


def _volume_ranking_snapshot(analyzed_items: list) -> list:
    return volume_ranking_snapshot_stage(analyzed_items)


def build_furniture_specs_json(analyzed_items: list) -> dict:
    return build_furniture_specs_json_stage(
        analyzed_items,
        normalize_dims_dict=_normalize_dims_dict,
        dims_has_positive_values=_dims_has_positive_values,
        parse_object_dimensions_mm=parse_object_dimensions_mm,
        is_rug_like=_is_rug_like,
        canonical_category=_canonical_category,
    )

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

def _extract_qty_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    try:
        t = str(text).lower()
        patterns = [
            r"\bqty\s*[:=]?\s*(\d+)\b",
            r"\bx\s*(\d+)\b",
            r"\b(\d+)\s*(?:ea|pcs|pieces|媛?\b",
        ]
        for pat in patterns:
            m = re.search(pat, t)
            if m:
                q = int(m.group(1))
                if q > 0:
                    return q
    except Exception:
        pass
    return None

def _summarize_items_for_ranking(items: list, max_items: int = 30) -> str:
    return summarize_items_for_ranking_support(items, max_items=max_items)

def _rank_best_variant_flash(
    candidate_paths: list,
    analyzed_items: list,
    *,
    timeout_sec: Optional[int] = None,
    max_attempts: Optional[int] = None,
) -> Optional[int]:
    return rank_best_variant_flash_support(
        candidate_paths,
        analyzed_items,
        call_gemini_with_failover=call_gemini_with_failover,
        rank_model_name=RANK_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
        timeout_sec=timeout_sec,
        max_attempts=max_attempts,
    )


def detect_back_wall_span_norm(empty_room_path: str) -> tuple:
    return detect_back_wall_span_norm_support(
        empty_room_path,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
    )

def detect_windows_present(room_path: str) -> bool:
    return detect_windows_present_support(
        room_path,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
    )


def _crop_ref_item_image(ref_path: str, box_2d: list, out_path: str):
    return crop_ref_item_image_support(ref_path, box_2d, out_path)

def _room_dims_valid(room_dims: dict) -> bool:
    return room_dims_valid_support(room_dims)

def create_scale_guide_overlay_with_model(
    empty_room_path: str,
    out_path: str,
    room_dims: Optional[Dict[str, Any]] = None,
):
    return create_scale_guide_overlay_with_model_support(
        empty_room_path,
        out_path,
        room_dims=room_dims,
        room_dims_valid_fn=_room_dims_valid,
        allow_all_safety_settings=allow_all_safety_settings,
        call_gemini_with_failover=call_gemini_with_failover,
        model_name=GEMINI_IMAGE_MODEL_NAME,
        logger=logger,
    )

def detect_primary_bbox_norm(staged_path: str, ref_item_crop_path: Optional[str], primary_label: Optional[str]):
    return detect_primary_bbox_norm_support(
        staged_path,
        ref_item_crop_path,
        primary_label,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
    )

def detect_item_bbox_norm(
    staged_path: str,
    ref_item_crop_path: Optional[str],
    item_label: Optional[str],
    item_context: Optional[dict] = None,
    timeout_sec: Optional[float] = None,
):
    return detect_item_bbox_norm_support(
        staged_path,
        ref_item_crop_path,
        item_label,
        item_context=item_context,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
        timeout_sec=timeout_sec or 70.0,
    )

def _score_scale(bbox_norm: tuple, wall_span_norm: tuple, target_ratio: float) -> float:
    return score_scale_support(bbox_norm, wall_span_norm, target_ratio)

def reorder_by_scale_best_pick(result_urls: list, ref_path: str, primary: dict, room_dims: dict, wall_span_norm: tuple) -> list:
    return reorder_by_scale_best_pick_support(
        result_urls,
        ref_path,
        primary,
        room_dims,
        wall_span_norm,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
    )

def validate_furnished_scale(
    staged_path: str,
    furniture_specs_json: dict,
    room_dims: dict,
    room_planes: Optional[dict],
    primary_label: Optional[str] = None,
    include_diagnostics: bool = False,
    scale_plan: Optional[dict] = None,
    geometry_contract: Optional[dict] = None,
    focus_item_keys: Optional[list[str]] = None,
    skip_reference_review: bool = False,
    absolute_deadline_ts: Optional[float] = None,
    remap_detect_timeout_sec: Optional[int] = None,
    remap_detect_retry: Optional[int] = None,
):
    return validate_furnished_scale_support(
        staged_path,
        furniture_specs_json,
        room_dims,
        room_planes,
        primary_label=primary_label,
        include_diagnostics=include_diagnostics,
        scale_plan=scale_plan,
        geometry_contract=geometry_contract,
        focus_item_keys=focus_item_keys,
        skip_reference_review=skip_reference_review,
        detect_furniture_boxes=detect_furniture_boxes,
        remap_model_name=REMAP_MODEL_NAME,
        remap_detect_timeout_sec=int(remap_detect_timeout_sec or REMAP_DETECT_TIMEOUT_SEC),
        remap_detect_retry=int(REMAP_DETECT_RETRY if remap_detect_retry is None else remap_detect_retry),
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
        log_brief=LOG_BRIEF,
        logger=logger,
        absolute_deadline_ts=absolute_deadline_ts,
    )

def detect_furniture_boxes(
    moodboard_path,
    model_name: Optional[str] = None,
    timeout_sec: Optional[int] = None,
    max_attempts: Optional[int] = None,
):
    return detect_furniture_boxes_stage(
        moodboard_path,
        log_brief=LOG_BRIEF,
        call_gemini_with_failover=call_gemini_with_failover,
        default_model_name=DETECT_FURNITURE_MODEL_NAME,
        model_name=model_name,
        timeout_sec=timeout_sec,
        max_attempts=max_attempts,
    )


def _normalize_label_for_match(label: str) -> str:
    return normalize_label_for_match_support(label)


def _canonical_category(raw: Optional[str]) -> str:
    return canonical_category_support(raw)


def _safe_key_token(raw: Optional[str], fallback: str = "na", max_len: int = 24) -> str:
    return safe_key_token_support(raw, fallback=fallback, max_len=max_len)


def _build_item_target_key(source: str, index: int, label: Optional[str] = None, category: Optional[str] = None, item_id: Optional[str] = None) -> str:
    return build_item_target_key_support(
        source,
        index,
        label=label,
        category=category,
        item_id=item_id,
    )


def _label_match_score(src_label: str, dst_label: str) -> float:
    return label_match_score_support(src_label, dst_label)


def _remap_match_score(src_item: dict, det_item: dict, src_idx: int, det_idx: int) -> float:
    return remap_match_score_support(src_item, det_item, src_idx, det_idx)


def _refresh_item_boxes_from_main_render(
    render_path: str,
    analyzed_items: list,
    *,
    remap_detect_timeout_sec: Optional[int] = None,
    remap_detect_retry: Optional[int] = None,
    remap_detect_max_attempts: Optional[int] = None,
) -> list:
    return refresh_item_boxes_from_main_render_support(
        render_path,
        analyzed_items,
        detect_furniture_boxes=detect_furniture_boxes,
        remap_model_name=REMAP_MODEL_NAME,
        remap_detect_timeout_sec=int(remap_detect_timeout_sec or REMAP_DETECT_TIMEOUT_SEC),
        remap_detect_retry=int(REMAP_DETECT_RETRY if remap_detect_retry is None else remap_detect_retry),
        remap_detect_max_attempts=remap_detect_max_attempts,
    )


def _normalize_dims_dict(raw: dict) -> dict:
    return normalize_dims_dict_support(raw)


def _dims_has_positive_values(dm: dict) -> bool:
    return dims_has_positive_values_support(dm)

def _is_two_dim_ok_label(label: str) -> bool:
    return is_two_dim_ok_label_support(label)

def _available_dim_axes(dm: dict) -> set:
    return available_dim_axes_support(dm)

def _dims_to_str(dims: dict) -> str:
    return dims_to_str_support(dims)

def _crop_item_with_padding(moodboard_path, item_data, unique_id=None, item_index=None, save_crop=True):
    return crop_item_with_padding_stage(
        moodboard_path,
        item_data,
        unique_id=unique_id,
        item_index=item_index,
        save_crop=save_crop,
    )

def analyze_room_structure(room_path, room_dimensions=None, timeout=120, max_attempts: Optional[int] = None):
    return analyze_room_structure_stage(
        room_path,
        room_dimensions=room_dimensions,
        timeout=timeout,
        max_attempts=max_attempts,
        call_gemini_with_failover=call_gemini_with_failover,
        model_name=ROOM_ONLY_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
    )


def analyze_room_and_items_long(room_path, items, room_dimensions=None, timeout=150):
    return analyze_room_and_items_long_stage(
        room_path,
        items,
        room_dimensions=room_dimensions,
        timeout=timeout,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        safe_json_from_model_text=_safe_json_from_model_text,
    )

def analyze_cropped_item(
    moodboard_path,
    item_data,
    unique_id=None,
    item_index=None,
    save_crop=True,
    enable_text_read=True,
    analysis_profile: str | None = None,
    allow_reference_feature_model: bool = False,
    provided_dims_mm=None,
    absolute_deadline_ts: Optional[float] = None,
):
    return analyze_cropped_item_stage(
        moodboard_path,
        item_data,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        safe_extract_json=_safe_extract_json,
        normalize_dims_dict=_normalize_dims_dict,
        log_brief=LOG_BRIEF,
        unique_id=unique_id,
        item_index=item_index,
        save_crop=save_crop,
        enable_text_read=enable_text_read,
        analysis_profile=analysis_profile,
        allow_reference_feature_model=allow_reference_feature_model,
        provided_dims_mm=provided_dims_mm,
        absolute_deadline_ts=absolute_deadline_ts,
    )

# [NEW] 엔드포인트: 도면 업로드 대신 -> 그냥 사진들만 업로드
# -----------------------------------------------------------------------------
# Generation Logic
# -----------------------------------------------------------------------------

def generate_empty_room(image_path, unique_id, start_time, stage_name="Stage 1", return_raw: bool = False):
    return generate_empty_room_stage(
        image_path,
        unique_id,
        start_time,
        stage_name=stage_name,
        return_raw=return_raw,
        total_timeout_limit=TOTAL_TIMEOUT_LIMIT,
        log_step=log_step,
        model_name=MAIN_IMAGE_MODEL_NAME,
        build_empty_room_prompt=build_empty_room_prompt,
        allow_all_safety_settings=allow_all_safety_settings,
        call_image_with_failover=CALL_MAIN_IMAGE_WITH_PROVIDER,
        match_aspect_to_target=match_aspect_to_target,
    )

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
    scale_plan=None,
    geometry_contract=None,
    scene_contract=None,
    placement_plan=None,
    start_time=0,
    room_planes=None,
    windows_present=None,
    room_analysis_text=None,
    enable_scale_check=False,
    max_generation_attempts=None,
):
    return generate_furnished_room_stage(
        room_path,
        style_prompt,
        ref_path,
        unique_id,
        furniture_specs=furniture_specs,
        furniture_specs_json=furniture_specs_json,
        room_dimensions=room_dimensions,
        placement_instructions=placement_instructions,
        scale_guide_path=scale_guide_path,
        primary_item=primary_item,
        room_dims_parsed=room_dims_parsed,
        wall_span_norm=wall_span_norm,
        size_hierarchy=size_hierarchy,
        scale_plan=scale_plan,
        geometry_contract=geometry_contract,
        scene_contract=scene_contract,
        placement_plan=placement_plan,
        start_time=start_time,
        room_planes=room_planes,
        windows_present=windows_present,
        room_analysis_text=room_analysis_text,
        enable_scale_check=enable_scale_check,
        max_generation_attempts=max_generation_attempts,
        total_timeout_limit=TOTAL_TIMEOUT_LIMIT,
        detect_windows_present=detect_windows_present,
        logger=logger,
        parse_room_dimensions_mm=parse_room_dimensions_mm,
        normalize_dims_dict=_normalize_dims_dict,
        is_two_dim_ok_label=_is_two_dim_ok_label,
        available_dim_axes=_available_dim_axes,
        summary_ref=SUMMARY_REF,
        log_brief=LOG_BRIEF,
        log_summary=LOG_SUMMARY,
        allow_all_safety_settings=allow_all_safety_settings,
        call_generation_with_failover=CALL_MAIN_IMAGE_WITH_PROVIDER,
        generation_model_name=MAIN_IMAGE_MODEL_NAME,
        match_aspect_to_target=match_aspect_to_target,
        validate_furnished_scale=validate_furnished_scale,
    )


def call_magnific_api(image_path, unique_id, start_time):
    return call_magnific_api_impl(
        image_path,
        unique_id,
        start_time,
        magnific_api_key=MAGNIFIC_API_KEY,
        magnific_endpoint=MAGNIFIC_ENDPOINT,
        total_timeout_limit=TOTAL_TIMEOUT_LIMIT,
        standardize_image=standardize_image,
        set_png_dpi=_set_png_dpi,
    )


def pad_image_to_target_canvas(
    img: Image.Image,
    target_w: int,
    target_h: int,
    pad_color: tuple = (255, 255, 255),
) -> Image.Image:
    return pad_image_to_target_canvas_shared(
        img,
        target_w,
        target_h,
        pad_color=pad_color,
    )

@app.get("/version.json")
async def version_json():
    return JSONResponse({"version": APP_BUILD_ID}, headers={"Cache-Control": "no-store"})

@app.get("/")
async def read_index(): return FileResponse(STATIC_DIR / "index.html")

# [NEW] Image Studio Page Route
@app.get("/image-studio")
def image_studio_page():
    return FileResponse(STATIC_DIR / "image_studio.html")

# Video Studio (separate page)
@app.get("/video-studio")
def video_studio_page():
    # Standalone page so users can build videos from existing images without re-rendering
    return FileResponse(STATIC_DIR / "video_studio.html")

@app.get("/api/outputs/list")
def api_outputs_list(request: Request, limit: int = 200):
    """List recently generated/uploaded images in /outputs for Video Studio selection."""
    guard = _require_outputs_api_access(request)
    if guard is not None:
        return guard
    limit = max(1, min(int(limit or 200), 500))
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    items = []
    for p in OUTPUTS_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            st = p.stat()
            rel = p.relative_to(OUTPUTS_DIR).as_posix()
            try:
                url = _resolve_video_studio_upload_url(p)
            except Exception:
                if S3_REQUIRED:
                    continue
                url = f"/outputs/{rel}"
            items.append({"filename": rel, "url": url, "local_url": f"/outputs/{rel}", "mtime": st.st_mtime})

    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items[:limit]}

@app.post("/api/outputs/upload")
@async_wrap
async def api_outputs_upload(request: Request, file: UploadFile = File(...)):
    """Upload an image to /outputs and return a URL usable by the video pipeline."""
    guard = _require_outputs_api_access(request)
    if guard is not None:
        return guard
    return await _store_output_upload(
        file,
        default_name="upload.png",
        allowed_exts=OUTPUTS_ALLOWED_EXTS,
        max_bytes=OUTPUTS_UPLOAD_MAX_BYTES,
        max_mb=OUTPUTS_UPLOAD_MAX_MB,
    )


@app.post("/api/outputs/upload-video")
@async_wrap
async def api_outputs_upload_video(request: Request, file: UploadFile = File(...)):
    """Upload a video clip to /outputs for Video Studio assemble workflows."""
    guard = _require_outputs_api_access(request)
    if guard is not None:
        return guard
    return await _store_output_upload(
        file,
        default_name="upload.mp4",
        allowed_exts=OUTPUTS_VIDEO_ALLOWED_EXTS,
        max_bytes=OUTPUTS_VIDEO_UPLOAD_MAX_BYTES,
        max_mb=OUTPUTS_VIDEO_UPLOAD_MAX_MB,
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return FileResponse(STATIC_DIR / "favicon-light.png")

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

    base_dir = STATIC_DIR / "thumbnails"
    if not base_dir.exists(): return []

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
                        valid_items.append({"index": int(num_part), "file": f})
                except: continue

        # 번호 순서대로 정렬
        valid_items.sort(key=lambda x: x["index"])
        return valid_items
    except Exception as e:
        print(f"Thumbnail Scan Error: {e}")
        return []

# --- 메인 렌더링 엔드포인트 ---
def render_room(
    file: UploadFile = File(...),
    room: str = Form(...),
    style: str = Form(...),
    variant: str = Form(...),
    moodboard: UploadFile = File(None),
    dimensions: str = Form(""),
    placement: str = Form(""),
    audience: str = Form(""),
    moodboard_items: Optional[List[Dict[str, Any]]] = None,
    item_analysis_profile: str = "detailed",
    simple_generation_mode: bool = False,
    precomputed_empty_room_path: str | None = None,
    precomputed_empty_room_raw_path: str | None = None,
):
    try:
        payload = run_render_room_workflow(
            RenderWorkflowRequest(
                file=file,
                room=room,
                style=style,
                variant=variant,
                moodboard=moodboard,
                dimensions=dimensions,
                placement=placement,
                audience=audience,
                moodboard_items=moodboard_items,
                item_analysis_profile=item_analysis_profile,
                simple_generation_mode=bool(simple_generation_mode),
                precomputed_empty_room_path=precomputed_empty_room_path,
                precomputed_empty_room_raw_path=precomputed_empty_room_raw_path,
            ),
            RenderWorkflowDependencies(
                runtime=RenderWorkflowRuntime(
                    style_map=STYLES,
                    generate_unique_id=lambda: uuid.uuid4().hex[:8],
                    time_now=time.time,
                    log_section=log_section,
                    summary_ref=SUMMARY_REF,
                    reset_summary_token=_reset_summary_token,
                    logger=logger,
                    log_brief=LOG_BRIEF,
                    log_summary=LOG_SUMMARY,
                    use_s3_moodboard=USE_S3_MOODBOARD,
                    max_concurrency_analysis=GEMINI_MAX_CONCURRENCY_ANALYSIS,
                    cart_max_analysis_workers=CART_MAX_ANALYSIS_WORKERS,
                    total_timeout_limit_sec=TOTAL_TIMEOUT_LIMIT,
                ),
                storage=RenderWorkflowStorageServices(
                    normalize_audience=_normalize_audience,
                    build_s3_prefix=_build_s3_prefix,
                    standardize_image=standardize_image,
                    materialize_input=_materialize_input,
                    resolve_image_url=resolve_image_url,
                    find_s3_moodboard_key=_find_s3_moodboard_key,
                    s3_public_url=_s3_public_url,
                ),
                analysis=RenderWorkflowAnalysisServices(
                    parse_room_dimensions_mm=parse_room_dimensions_mm,
                    room_dims_valid_fn=_room_dims_valid,
                    build_explicit_room_dims_contract=build_explicit_room_dims_contract_stage,
                    estimate_room_dims_contract=estimate_room_dims_contract_stage,
                    build_scene_contract=build_scene_contract_stage,
                    build_geometry_contract=build_geometry_contract_stage,
                    build_product_identity_bundle=build_product_identity_bundle_stage,
                    build_archetype_strategies=build_archetype_strategies_stage,
                    build_placement_plan=build_placement_plan_stage,
                    build_item_target_key=_build_item_target_key,
                    canonical_category=_canonical_category,
                    detect_furniture_boxes=detect_furniture_boxes,
                    analyze_room_structure=analyze_room_structure,
                    analyze_cropped_item=analyze_cropped_item,
                    normalize_dims_dict=_normalize_dims_dict,
                    parse_object_dimensions_mm=parse_object_dimensions_mm,
                    build_furniture_specs_json=build_furniture_specs_json,
                    create_scale_guide_overlay_with_model=create_scale_guide_overlay_with_model,
                    match_aspect_to_target=match_aspect_to_target,
                ),
                generation=RenderWorkflowGenerationServices(
                    generate_empty_room=generate_empty_room,
                    generate_furnished_room=generate_furnished_room,
                ),
                postprocess=RenderWorkflowPostprocessServices(
                    rank_best_variant=_rank_best_variant_flash,
                    refresh_item_boxes_from_main_render=_refresh_item_boxes_from_main_render,
                    attach_volume_ranks=_attach_volume_ranks,
                    volume_ranking_snapshot=_volume_ranking_snapshot,
                ),
            ),
        )
        return JSONResponse(content=payload)

    except Exception as e:
        print(f"\n🔥🔥🔥 [SERVER CRASH] {e}", flush=True)
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)

def _queue_route_deps() -> QueueRouteDependencies:
        return QueueRouteDependencies(
        redis_url=REDIS_URL,
        local_inline_queue_enabled=LOCAL_INLINE_QUEUE_ENABLED,
        rq_queue_render=RQ_QUEUE_RENDER,
        rq_queue_upscale=RQ_QUEUE_UPSCALE,
        cart_max_items=CART_MAX_ITEMS,
        api_auth_disabled=API_AUTH_DISABLED,
        internal_api_keys=INTERNAL_INTEA_API_KEYS,
        external_api_keys=EXTERNAL_INTEA_API_KEYS,
        enqueue_job=_enqueue_job,
        fetch_job=_fetch_job,
        load_job_result_s3=_load_job_result_s3,
        load_preset_map=_load_preset_map,
        require_role=require_role,
        apply_cart_limits=apply_cart_limits,
        build_cart_summary=build_cart_summary,
        materialize_input=_materialize_input,
        normalize_item_image=lambda local_path, unique_id, index: _normalize_item_image(local_path, unique_id, index, max_size=1024),
        resolve_image_url=resolve_image_url,
        build_s3_prefix=_build_s3_prefix,
        build_item_target_key=_build_item_target_key,
        parse_internal_render_items_form=parse_internal_render_items_form,
        persist_internal_room_upload=persist_internal_room_upload,
        persist_internal_item_uploads=persist_internal_item_uploads,
        persist_internal_item_source_uploads=persist_internal_item_source_uploads,
        prepare_internal_item_upload_paths=prepare_internal_item_upload_paths,
        persist_internal_media_uploads=persist_internal_media_uploads,
        build_internal_render_job_payload=build_internal_render_job_payload,
        build_internal_itemized_async_render_job_payload=build_internal_itemized_async_render_job_payload,
        build_image_edit_job_payload=build_image_edit_job_payload,
        build_frontal_view_job_payload=build_frontal_view_job_payload,
        build_upscale_job_payload=build_upscale_job_payload,
        build_finalize_download_job_payload=build_finalize_download_job_payload,
        build_empty_room_job_payload=build_empty_room_job_payload,
        build_external_preset_job=build_external_preset_job,
        build_external_cart_job=build_external_cart_job,
        build_external_cart_batch_job=build_external_cart_batch_job,
        build_external_render_video_job=build_external_render_video_job,
        build_regenerate_detail_job_payload=build_regenerate_detail_job_payload,
        build_detail_generation_job_payload=build_detail_generation_job_payload,
        rq_video_job_timeout=RQ_VIDEO_JOB_TIMEOUT,
        job_render=job_render,
        job_render_with_details=job_render_with_details,
        job_render_with_extra=job_render_with_extra,
        job_render_cart_simple_batch=job_render_cart_simple_batch,
        job_generate_render_video=job_generate_render_video,
        job_image_edit=job_image_edit,
        job_frontal_view=job_frontal_view,
        job_upscale=job_upscale,
        job_finalize=job_finalize,
        job_generate_empty_room=job_generate_empty_room,
        job_regenerate_single_detail=job_regenerate_single_detail,
        job_generate_details=job_generate_details,
        set_staging_job=_set_staging_job,
        update_staging_job=_update_staging_job,
        get_staging_job=_get_staging_job,
        start_background_task=_start_background_task,
    )

@app.get("/download")
def download_proxy(url: str, request: Request):
    if not url:
        return JSONResponse(content={"error": "url is required"}, status_code=400)

    local_path = _resolve_public_file_path(url)
    if local_path is not None:
        if not local_path.exists():
            return JSONResponse(content={"error": "File not found"}, status_code=404)
        filename = local_path.name or "download"
        return FileResponse(local_path, filename=filename)

    if not _is_allowed_download_url(url, request):
        return JSONResponse(content={"error": "URL not allowed"}, status_code=403)

    try:
        resp = requests.get(url, stream=True, timeout=30)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=502)

    if not resp.ok:
        return JSONResponse(content={"error": f"Upstream error ({resp.status_code})"}, status_code=resp.status_code)

    content_type = resp.headers.get("content-type") or "application/octet-stream"
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path) or "download"
    headers = {"Content-Disposition": f'attachment; filename=\"{filename}\"'}
    return StreamingResponse(resp.iter_content(chunk_size=1024 * 1024), media_type=content_type, headers=headers)

@app.get("/jobs/{job_id}")
def get_job_status(job_id: str, compact: bool = False):
    return handle_get_job_status(job_id, deps=_queue_route_deps(), compact=compact)

@app.post("/async/render")
@async_wrap
def render_room_async(
    file: UploadFile = File(...),
    room: str = Form(...),
    style: str = Form(...),
    variant: str = Form(...),
    items_json: str = Form(...),
    item_images: List[UploadFile] = File(...),
    dimensions: str = Form(""),
    placement: str = Form(""),
):
    return handle_render_room_async(
        file=file,
        room=room,
        style=style,
        variant=variant,
        items_json=items_json,
        item_images=item_images,
        dimensions=dimensions,
        placement=placement,
        deps=_queue_route_deps(),
    )

@app.post("/async/generate-image-edit")
@async_wrap
def generate_image_edit_async(
    input_photos: List[UploadFile] = File(...),
    instructions: str = Form(...),
    mode: str = Form(...),
    mask: UploadFile = File(None),
):
    return handle_generate_image_edit_async(
        input_photos=input_photos,
        instructions=instructions,
        mode=mode,
        mask=mask,
        deps=_queue_route_deps(),
    )

@app.post("/async/generate-frontal-view")
@async_wrap
def generate_frontal_view_async(
    input_photos: List[UploadFile] = File(...),
):
    return handle_generate_frontal_view_async(input_photos=input_photos, deps=_queue_route_deps())

@app.post("/async/upscale")
@async_wrap
def upscale_and_download_async(req: UpscaleRequest):
    return handle_upscale_async(req, deps=_queue_route_deps())

@app.post("/async/finalize-download")
@async_wrap
def finalize_download_async(req: FinalizeRequest):
    return handle_finalize_async(req, deps=_queue_route_deps())

@app.post("/async/generate-empty-room")
@async_wrap
def generate_empty_room_async(req: FinalizeRequest):
    return handle_generate_empty_room_async(req, deps=_queue_route_deps())

@app.post("/api/internal/render")
@async_wrap
def api_internal_render(req: InternalRenderRequest, request: Request):
    return handle_api_internal_render(req, request, deps=_queue_route_deps())

@app.post("/api/external/render/preset")
@async_wrap
def api_external_render_preset(req: PresetRenderRequest, request: Request):
    return handle_api_external_render_preset(req, request, deps=_queue_route_deps())

@app.post("/api/external/render/cart")
@async_wrap
def api_external_render_cart(req: CartRenderRequest, request: Request):
    return handle_api_external_render_cart(req, request, deps=_queue_route_deps())

@app.post("/api/external/render/cart-simple")
@async_wrap
def api_external_render_cart_simple(req: CartRenderRequest, request: Request):
    return handle_api_external_render_cart_simple(req, request, deps=_queue_route_deps())

@app.post("/api/external/render/cart-simple-batch")
@async_wrap
def api_external_render_cart_simple_batch(req: CartSimpleBatchRequest, request: Request):
    return handle_api_external_render_cart_simple_batch(req, request, deps=_queue_route_deps())

@app.post("/api/external/render/video")
@async_wrap
def api_external_render_video(req: ExternalRenderVideoRequest, request: Request):
    return handle_api_external_render_video(req, request, deps=_queue_route_deps())

def finalize_download(req: FinalizeRequest):
    return job_entrypoints_module.finalize_download(req)

def upscale_and_download(req: UpscaleRequest):
    return job_entrypoints_module.upscale_and_download(req)

@app.post("/regenerate-single-detail")
@async_wrap
def regenerate_single_detail(req: RegenerateDetailRequest):
    return handle_regenerate_single_detail(req, deps=_queue_route_deps())

# [수정] main.py 내부의 generate_details_endpoint 함수 교체

@app.post("/generate-details")
@async_wrap
def generate_details_endpoint(req: DetailRequest):
    return handle_generate_details(req, deps=_queue_route_deps())

@app.post("/generate-moodboard-options")
@async_wrap
def generate_moodboard_options(
    file: UploadFile = File(...),
    audience: str = Form("")
):
    result = run_generate_moodboard_options(
        file,
        audience,
        normalize_audience=_normalize_audience,
        build_s3_prefix=_build_s3_prefix,
        resolve_image_url=resolve_image_url,
        log_section=log_section,
        detect_furniture_boxes=detect_furniture_boxes,
        build_prompt=build_moodboard_generation_prompt,
        allow_all_safety_settings=allow_all_safety_settings,
        call_gemini_with_failover=call_gemini_with_failover,
        model_name=GEMINI_IMAGE_MODEL_NAME,
    )
    if result.get("error"):
        return JSONResponse(content=result, status_code=500)
    return JSONResponse(content=result)


# =========================
# Video MVP (Kling Image-to-Video via Freepik API)
# =========================
# Use Freepik API key for Kling as well (same header: x-freepik-api-key)
FREEPIK_API_KEY = os.getenv("FREEPIK_API_KEY") or os.getenv("MAGNIFIC_API_KEY")  # fallback for existing env
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-6-pro")  # e.g. kling-v2-1-pro, kling-v2-6-pro
KLING_ENDPOINT = os.getenv("KLING_ENDPOINT", build_kling_endpoint(KLING_MODEL))

# Concurrency controls for provider-side Kling clip generation.
VIDEO_MAX_CONCURRENCY = int(os.getenv("VIDEO_MAX_CONCURRENCY", "4"))
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

def _freepik_kling_create_task(image_b64: str, prompt: str, negative_prompt: str, duration: str, cfg_scale: float) -> str:
    return create_kling_task_impl(
        image_b64,
        prompt,
        negative_prompt,
        duration,
        cfg_scale,
        freepik_api_key=FREEPIK_API_KEY,
        kling_endpoint=KLING_ENDPOINT,
        video_semaphore=_video_sem,
    )


def _freepik_kling_poll(
    task_id: str,
    *,
    clip_index: int,
    total_clips: int,
    update_job_status,
    status_callback=None,
    timeout_sec: int = 1800,
) -> str:
    return poll_kling_task_impl(
        task_id,
        clip_index=clip_index,
        total_clips=total_clips,
        freepik_api_key=FREEPIK_API_KEY,
        kling_endpoint=KLING_ENDPOINT,
        video_semaphore=_video_sem,
        update_job_status=update_job_status,
        status_callback=status_callback,
        timeout_sec=timeout_sec,
    )

job_entrypoints_module.configure_job_entrypoints(
    JobEntrypointServices(
        normalize_audience=_normalize_audience,
        save_job_result=_save_job_result_s3,
        materialize_input=_materialize_input,
        normalize_item_image=lambda local_path, unique_id, index: _normalize_item_image(local_path, unique_id, index, max_size=1024),
        standardize_image=standardize_image,
        build_s3_prefix=_build_s3_prefix,
        resolve_image_url=resolve_image_url,
        render_room=render_room,
        generate_empty_room=generate_empty_room,
        call_magnific_api=call_magnific_api,
        s3_prefix_from_url=_s3_prefix_from_url,
        process_image_edit_logic=lambda photo_paths, instructions, mode, unique_id, index, mask_path=None: process_image_edit_logic_stage(
            photo_paths,
            instructions,
            mode,
            unique_id,
            index,
            build_image_edit_step_prompt=build_image_edit_step_prompt,
            pad_image_to_target_canvas=pad_image_to_target_canvas,
            call_gemini_with_failover=CALL_REPAIR_IMAGE_WITH_PROVIDER,
            model_name=REPAIR_IMAGE_MODEL_NAME,
            match_aspect_to_target=match_aspect_to_target,
            mask_path=mask_path,
        ),
        generate_frontal_room_from_photos=lambda photo_paths, unique_id, index: generate_frontal_room_from_photos_stage(
            photo_paths,
            unique_id,
            index,
            build_frontal_analysis_prompt=build_frontal_analysis_prompt,
            build_frontal_generation_prompt=build_frontal_generation_prompt,
            call_gemini_with_failover=call_gemini_with_failover,
            analysis_model_name=ANALYSIS_MODEL_NAME,
            model_name=REPAIR_IMAGE_MODEL_NAME,
            allow_all_safety_settings=allow_all_safety_settings,
            standardize_image=standardize_image,
            call_generation_with_failover=CALL_REPAIR_IMAGE_WITH_PROVIDER,
        ),
        log_section=log_section,
        detect_furniture_boxes=detect_furniture_boxes,
        detect_item_bbox_norm=detect_item_bbox_norm,
        canonical_category=_canonical_category,
        build_item_target_key=_build_item_target_key,
        analyze_cropped_item=analyze_cropped_item,
        attach_volume_ranks=_attach_volume_ranks,
        construct_dynamic_styles=construct_dynamic_styles_stage,
        generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: generate_detail_view_stage(
            original_image_path,
            style_config,
            unique_id,
            index,
            furniture_data,
            **kwargs,
            materialize_input=_materialize_input,
            normalize_label_for_match=_normalize_label_for_match,
            allow_harassment_only_safety_settings=allow_harassment_only_safety_settings,
            call_gemini_with_failover=CALL_REPAIR_IMAGE_WITH_PROVIDER,
            model_name=REPAIR_IMAGE_MODEL_NAME,
        ),
        normalize_label_for_match=_normalize_label_for_match,
        volume_ranking_snapshot=_volume_ranking_snapshot,
        finalize_request_factory=FinalizeRequest,
        upscale_request_factory=UpscaleRequest,
        max_concurrency_analysis=GEMINI_MAX_CONCURRENCY_ANALYSIS,
        fetch_job=lambda job_id: _fetch_job(job_id),
        load_job_result=lambda job_id: _load_job_result_s3(job_id),
        queue_source_generation_job=queue_source_generation_job,
        queue_final_compile_job=queue_final_compile_job,
        get_video_job=get_video_job,
        create_kling_task=lambda image_b64, prompt, negative_prompt, duration, cfg_scale: _freepik_kling_create_task(
            image_b64,
            prompt,
            negative_prompt,
            duration,
            cfg_scale,
        ),
        poll_kling_task=lambda task_id, **kwargs: _freepik_kling_poll(task_id, **kwargs),
        video_target_fps=VIDEO_TARGET_FPS,
        video_max_concurrency=VIDEO_MAX_CONCURRENCY,
    )
)


def _current_video_job_id(payload: dict) -> str:
    job = get_current_job()
    if job:
        return str(job.id)
    return str(payload.get("job_id") or uuid.uuid4().hex)


def job_video_generate_sources(payload: dict) -> dict:
    job_id = _current_video_job_id(payload)
    req = SourceGenRequest(**payload)
    run_source_generation_job(
        job_id,
        req.items,
        req.cfg_scale,
        video_target_fps=VIDEO_TARGET_FPS,
        video_max_concurrency=VIDEO_MAX_CONCURRENCY,
        create_kling_task=_freepik_kling_create_task,
        poll_kling_task=_freepik_kling_poll,
        resolve_output_url=_resolve_video_output_url,
    )
    return _publish_video_job_state(
        job_id,
        get_video_job(job_id) or {"status": "FAILED", "error": "Video source job ended without state"},
    )


def job_video_compile(payload: dict) -> dict:
    job_id = _current_video_job_id(payload)
    req = CompileRequest(**payload)
    run_final_compile_job(job_id, req, video_target_fps=VIDEO_TARGET_FPS, resolve_output_url=_resolve_video_output_url)
    return _publish_video_job_state(
        job_id,
        get_video_job(job_id) or {"status": "FAILED", "error": "Video compile job ended without state"},
    )


def _publish_video_job_state(job_id: str, state: dict) -> dict:
    published = publish_video_state_outputs(state, resolve_output_url=_resolve_video_output_url)
    update_video_job(job_id, **published)
    return published


def _resolve_video_output_url(output_url: str) -> str | None:
    rel = output_url[len("/outputs/") :].lstrip("/\\")
    local_path = Path("outputs") / rel
    return resolve_image_url(
        str(local_path),
        s3_prefix_override=_build_s3_prefix("external", "videorendered", "rendered"),
    )


@app.post("/video-mvp/generate-sources")
@async_wrap
async def api_generate_sources(req: SourceGenRequest):
    job_id, err = enqueue_source_generation_rq_job(
        req,
        enqueue_job=_enqueue_job,
        queue_name=RQ_QUEUE_VIDEO,
        job_func=job_video_generate_sources,
    )
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return {"job_id": job_id, "status": "queued"}

@app.post("/video-mvp/compile")
@async_wrap
async def api_compile_final(req: CompileRequest):
    job_id, err = enqueue_compile_rq_job(
        req,
        enqueue_job=_enqueue_job,
        queue_name=RQ_QUEUE_VIDEO,
        job_func=job_video_compile,
    )
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return {"job_id": job_id, "status": "queued"}

@app.get("/video-mvp/status/{job_id}")
async def video_mvp_status(job_id: str):
    payload, status_code = build_video_status_payload(
        job_id,
        fetch_job=_fetch_job,
        load_memory_job=get_video_job,
    )
    if status_code != 200:
        return JSONResponse(payload, status_code=status_code)
    return payload


if __name__ == "__main__":
    import uvicorn
    reload_flag = os.getenv("DEV_RELOAD", "0") == "1"
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=reload_flag, log_level="info")

