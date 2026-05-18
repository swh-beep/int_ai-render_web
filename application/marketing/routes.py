from functools import lru_cache
import os

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from application.marketing.db import MarketingDatabaseConfigError, get_marketing_engine
from application.marketing.repository import MarketingReelsRepository
from application.marketing.schemas import (
    MarketingAudioPromptCreate,
    MarketingAudioSettingsUpdate,
    MarketingClipApprovalPayload,
    MarketingClipAttemptPayload,
    MarketingClipAttemptUpdate,
    MarketingClipGenerationCreate,
    MarketingClipPromptCreate,
    MarketingClipSourceImagesUpdate,
    MarketingFinalResultPayload,
    MarketingGlobalPromptCreate,
    MarketingGroupTitleUpdate,
    MarketingReelGroupCreate,
)


health_router = APIRouter(prefix="/api/marketing", tags=["marketing"])
reel_groups_router = APIRouter(prefix="/api/marketing/reel-groups", tags=["marketing-reels"])
global_prompts_router = APIRouter(prefix="/api/marketing/global-prompts", tags=["marketing-prompts"])
clip_prompts_router = APIRouter(prefix="/api/marketing/clip-prompts", tags=["marketing-prompts"])
audio_prompts_router = APIRouter(prefix="/api/marketing/audio-prompts", tags=["marketing-prompts"])


@lru_cache(maxsize=1)
def get_marketing_repository() -> MarketingReelsRepository:
    repo = MarketingReelsRepository(get_marketing_engine())
    repo.ensure_schema()
    return repo


def reset_marketing_repository_cache() -> None:
    cache_clear = getattr(get_marketing_repository, "cache_clear", None)
    if cache_clear:
        cache_clear()


def _repo_or_503() -> MarketingReelsRepository:
    try:
        return get_marketing_repository()
    except MarketingDatabaseConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _profile_name() -> str:
    return (os.getenv("SPRING_PROFILES_ACTIVE") or os.getenv("APP_PROFILE") or os.getenv("MARKETING_DB_PROFILE") or "qa").strip()


@health_router.get("/db-health")
def marketing_db_health():
    try:
        engine = get_marketing_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except MarketingDatabaseConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Marketing DB connection check failed: {exc.__class__.__name__}",
        ) from exc
    return {"ok": True, "status": "connected", "database": "marketing"}


@health_router.post("/dev/migrate-clip-schema")
def migrate_marketing_clip_schema(apply: bool = False, create_missing: bool = False):
    if not _truthy_env("MARKETING_REELS_MIGRATION_API_ENABLED"):
        raise HTTPException(status_code=404, detail="Migration API is disabled")
    if _profile_name() == "real" and not _truthy_env("MARKETING_REELS_ALLOW_REAL_MIGRATION"):
        raise HTTPException(status_code=403, detail="Migration API is blocked for real profile")

    try:
        from scripts.migrate_marketing_reels_clip_schema import migrate_marketing_reels

        summary = migrate_marketing_reels(get_marketing_engine(), apply=apply, create_missing=create_missing)
    except MarketingDatabaseConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Marketing migration failed: {exc.__class__.__name__}") from exc

    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "planned": summary.planned,
        "inserted": summary.inserted,
        "warnings": summary.warnings,
    }


@reel_groups_router.post("")
def create_reel_group(payload: MarketingReelGroupCreate):
    return _repo_or_503().create_group(payload)


@reel_groups_router.patch("/{group_id}/failed")
def mark_reel_group_failed(group_id: str):
    try:
        _repo_or_503().mark_group_failed(group_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"group_id": group_id}


@reel_groups_router.patch("/{group_id}/clips/source-images")
def update_clip_source_images(group_id: str, payload: MarketingClipSourceImagesUpdate):
    try:
        return _repo_or_503().update_clip_source_images(group_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@reel_groups_router.post("/{group_id}/clip-generations")
def create_clip_generation(group_id: str, payload: MarketingClipGenerationCreate):
    try:
        return _repo_or_503().create_clip_generation(group_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@reel_groups_router.post("/{group_id}/clip-attempts")
def create_clip_attempt(group_id: str, payload: MarketingClipAttemptPayload):
    try:
        return _repo_or_503().upsert_clip_attempt(group_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@reel_groups_router.patch("/{group_id}/clip-attempts/{attempt_id}")
def update_clip_attempt(group_id: str, attempt_id: str, payload: MarketingClipAttemptUpdate):
    try:
        return _repo_or_503().update_clip_attempt(group_id, attempt_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@reel_groups_router.patch("/{group_id}/clips/{clip_id}/approval")
def approve_clip_attempt(group_id: str, clip_id: str, payload: MarketingClipApprovalPayload):
    try:
        return _repo_or_503().approve_clip_attempt(group_id, clip_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@reel_groups_router.patch("/{group_id}/clips/{clip_id}/deleted")
def mark_clip_deleted(group_id: str, clip_id: str):
    try:
        return _repo_or_503().mark_clip_deleted(group_id, clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@reel_groups_router.patch("/{group_id}/final")
def patch_final_result(group_id: str, payload: MarketingFinalResultPayload):
    try:
        return _repo_or_503().patch_final(group_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@reel_groups_router.patch("/{group_id}/title")
def update_reel_group_title(group_id: str, payload: MarketingGroupTitleUpdate):
    try:
        return _repo_or_503().update_group_title(group_id, payload.final_title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@reel_groups_router.patch("/{group_id}/audio-settings")
def update_reel_group_audio_settings(group_id: str, payload: MarketingAudioSettingsUpdate):
    try:
        return _repo_or_503().update_group_audio_settings(group_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@reel_groups_router.get("")
def list_reel_groups(limit: int = 20):
    return _repo_or_503().list_groups(limit)


@reel_groups_router.get("/{group_id}")
def get_reel_group(group_id: str):
    try:
        return _repo_or_503().get_group_detail(group_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@global_prompts_router.post("")
def create_global_prompt(payload: MarketingGlobalPromptCreate):
    return _repo_or_503().create_global_prompt(payload)


@global_prompts_router.get("")
def list_global_prompts(limit: int = 30):
    return _repo_or_503().list_global_prompts(limit)


@global_prompts_router.delete("/{prompt_id}")
def delete_global_prompt(prompt_id: str):
    try:
        return _repo_or_503().delete_global_prompt(prompt_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@clip_prompts_router.post("")
def create_clip_prompt(payload: MarketingClipPromptCreate):
    return _repo_or_503().create_clip_prompt(payload)


@clip_prompts_router.get("")
def list_clip_prompts(limit: int = 30):
    return _repo_or_503().list_clip_prompts(limit)


@clip_prompts_router.delete("/{prompt_id}")
def delete_clip_prompt(prompt_id: str):
    try:
        return _repo_or_503().delete_clip_prompt(prompt_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@audio_prompts_router.post("")
def create_audio_prompt(payload: MarketingAudioPromptCreate):
    return _repo_or_503().create_audio_prompt(payload)


@audio_prompts_router.get("")
def list_audio_prompts(limit: int = 30):
    return _repo_or_503().list_audio_prompts(limit)


@audio_prompts_router.delete("/{prompt_id}")
def delete_audio_prompt(prompt_id: str):
    try:
        return _repo_or_503().delete_audio_prompt(prompt_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


router = APIRouter()
router.include_router(health_router)
router.include_router(reel_groups_router)
router.include_router(global_prompts_router)
router.include_router(clip_prompts_router)
router.include_router(audio_prompts_router)
