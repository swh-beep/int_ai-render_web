import json
import os
import random
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

_HIGH_THINKING_LOG_TAGS = {
    "Analysis.CropItem",
    "Analysis.DetectFurniture",
    "Analysis.ItemBBox",
    "Analysis.PrimaryBBox",
    "Analysis.ReferenceFeatures",
    "Analysis.ReferenceFidelity",
    "Analysis.RoomAndItemsLong",
}
_MEDIUM_THINKING_LOG_TAGS = {
    "Analysis.BackWallSpan",
    "Analysis.DetectFurniture",
    "Analysis.RoomOnly",
    "Analysis.WindowsPresent",
    "Frontal.Analysis",
    "RankBestVariant",
}
_GENERATION_CONFIG_KEYS = (
    "candidate_count",
    "frequency_penalty",
    "max_output_tokens",
    "presence_penalty",
    "response_mime_type",
    "response_modalities",
    "response_schema",
    "seed",
    "stop_sequences",
    "temperature",
    "top_k",
    "top_p",
)
_IMAGE_CONFIG_KEYS = (
    "aspect_ratio",
    "image_size",
    "output_compression_quality",
    "output_mime_type",
)


def _iter_contents(contents: Sequence[Any] | Any) -> list[Any]:
    if contents is None:
        return []
    if isinstance(contents, (list, tuple)):
        return list(contents)
    return [contents]


def _normalize_enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name).strip()
    text = str(value or "").strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".")[-1]
    return text.strip()


def _convert_safety_settings(safety_settings: Any) -> list[types.SafetySetting] | None:
    if not safety_settings:
        return None
    entries: list[Any]
    if isinstance(safety_settings, dict):
        entries = [
            {"category": category, "threshold": threshold}
            for category, threshold in safety_settings.items()
        ]
    elif isinstance(safety_settings, list):
        entries = list(safety_settings)
    else:
        return None

    normalized: list[types.SafetySetting] = []
    for entry in entries:
        if isinstance(entry, types.SafetySetting):
            normalized.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        category_name = _normalize_enum_name(entry.get("category"))
        threshold_name = _normalize_enum_name(entry.get("threshold"))
        category = getattr(types.HarmCategory, category_name, None)
        threshold = getattr(types.HarmBlockThreshold, threshold_name, None)
        if category is None or threshold is None:
            continue
        normalized.append(
            types.SafetySetting(
                category=category,
                threshold=threshold,
            )
        )
    return normalized or None


def _is_image_generation_model(model_name: str) -> bool:
    normalized = str(model_name or "").strip().lower()
    return normalized.endswith("-image") or "-image-preview" in normalized


def _default_thinking_level(model_name: str, log_tag: str | None, explicit_value: Any) -> str | None:
    normalized_explicit = str(explicit_value or "").strip().lower()
    if normalized_explicit:
        return normalized_explicit
    if _is_image_generation_model(model_name):
        return None
    normalized_tag = str(log_tag or "").strip()
    if normalized_tag in _HIGH_THINKING_LOG_TAGS:
        return "high"
    if normalized_tag in _MEDIUM_THINKING_LOG_TAGS or normalized_tag.startswith("Analysis."):
        return "medium"
    return None


def _build_generation_config(
    *,
    model_name: str,
    request_options: dict[str, Any],
    safety_settings: Any,
    system_instruction: str | None,
    log_tag: str | None,
) -> dict[str, Any]:
    config: dict[str, Any] = {}
    timeout_raw = request_options.pop("timeout", None)
    request_options.pop("max_attempts", None)

    if timeout_raw is not None:
        try:
            timeout_ms = max(1, int(float(timeout_raw) * 1000))
        except Exception:
            timeout_ms = None
        if timeout_ms is not None:
            config["http_options"] = {"timeout": timeout_ms}

    if system_instruction:
        config["system_instruction"] = system_instruction

    normalized_safety_settings = _convert_safety_settings(safety_settings)
    if normalized_safety_settings:
        config["safety_settings"] = normalized_safety_settings

    for key in _GENERATION_CONFIG_KEYS:
        if key in request_options and request_options[key] is not None:
            config[key] = request_options.pop(key)

    thinking_level = _default_thinking_level(
        model_name,
        log_tag,
        request_options.pop("thinking_level", None),
    )
    include_thoughts = request_options.pop("include_thoughts", None)
    thinking_budget = request_options.pop("thinking_budget", None)
    if thinking_level or include_thoughts is not None or thinking_budget is not None:
        thinking_config: dict[str, Any] = {}
        if thinking_level:
            thinking_config["thinking_level"] = thinking_level
        if include_thoughts is not None:
            thinking_config["include_thoughts"] = bool(include_thoughts)
        if thinking_budget is not None:
            try:
                thinking_config["thinking_budget"] = int(thinking_budget)
            except Exception:
                pass
        if thinking_config:
            config["thinking_config"] = thinking_config

    if _is_image_generation_model(model_name):
        image_config: dict[str, Any] = {}
        for key in _IMAGE_CONFIG_KEYS:
            if key in request_options and request_options[key] is not None:
                image_config[key] = request_options.pop(key)
        if image_config:
            config["image_config"] = image_config
        config.setdefault("response_modalities", ["IMAGE"])

    return config


_QA_BUDGET_LOCK = threading.Lock()


def _parse_max_calls(max_calls: int | str | None) -> int | None:
    try:
        parsed = int(max_calls) if max_calls is not None and str(max_calls).strip() else 0
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _resolve_budget_path(budget_file: str | None = None) -> Path | None:
    raw = (budget_file or os.getenv("QA_GEMINI_BUDGET_FILE") or "").strip()
    return Path(raw).resolve() if raw else None


def get_qa_budget_snapshot(*, budget_file: str | None = None, max_calls: int | str | None = None) -> dict:
    limit = _parse_max_calls(max_calls if max_calls is not None else os.getenv("QA_GEMINI_MAX_CALLS"))
    path = _resolve_budget_path(budget_file)
    if path is None or limit is None:
        return {"enabled": False, "limit": limit or 0, "count": 0, "remaining": limit or 0, "events": []}

    if not path.exists():
        return {"enabled": True, "limit": limit, "count": 0, "remaining": limit, "events": [], "updated_at": None}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}

    count = int(payload.get("count") or 0)
    snapshot = {
        "enabled": True,
        "limit": limit,
        "count": count,
        "remaining": max(0, limit - count),
        "events": list(payload.get("events") or []),
        "updated_at": payload.get("updated_at"),
    }
    return snapshot


def _write_qa_budget_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _reserve_qa_budget_call(*, model_name: str, log_tag: str | None) -> dict | None:
    path = _resolve_budget_path()
    limit = _parse_max_calls(os.getenv("QA_GEMINI_MAX_CALLS"))
    if path is None or limit is None:
        return None

    with _QA_BUDGET_LOCK:
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
        else:
            current = {}

        count = int(current.get("count") or 0)
        if count >= limit:
            raise RuntimeError(f"QA Gemini budget exceeded ({count}/{limit})")

        next_count = count + 1
        updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        events = list(current.get("events") or [])
        events.append(
            {
                "index": next_count,
                "model_name": model_name,
                "log_tag": log_tag,
                "timestamp": updated_at,
            }
        )
        payload = {
            "count": next_count,
            "limit": limit,
            "remaining": max(0, limit - next_count),
            "updated_at": updated_at,
            "events": events,
        }
        _write_qa_budget_snapshot(path, payload)
        return payload


def call_gemini_with_failover(
    model_name: str,
    contents: Sequence[Any],
    request_options: dict,
    safety_settings: dict,
    *,
    api_key_pool: list[str],
    quota_exceeded_keys: set[str],
    logger: Any,
    log_brief: bool,
    system_instruction: str | None = None,
    log_tag: str | None = None,
):
    request_options = dict(request_options or {})
    try:
        max_attempts = max(1, int(request_options.pop("max_attempts", None) or 5))
    except Exception:
        request_options.pop("max_attempts", None)
        max_attempts = 5
    tag = f" tag={log_tag}" if log_tag else ""
    content_items = _iter_contents(contents)

    try:
        content_types = []
        for item in content_items:
            if isinstance(item, str):
                content_types.append(f"str({len(item)})")
            else:
                content_types.append(type(item).__name__)
        if not log_brief:
            logger.info(
                f"[Gemini] model={model_name} timeout={request_options.get('timeout')} contents={content_types}{tag}"
            )
    except Exception:
        pass

    for attempt in range(max_attempts):
        available_keys = [key for key in api_key_pool if key not in quota_exceeded_keys]
        if not available_keys:
            logger.warning("[Gemini] All keys locked. Cooldown 5s then reset.")
            time.sleep(5)
            quota_exceeded_keys.clear()
            available_keys = list(api_key_pool)

        current_key = random.choice(available_keys)
        masked_key = current_key[-4:]

        try:
            client = genai.Client(api_key=current_key)
            config = _build_generation_config(
                model_name=model_name,
                request_options=dict(request_options or {}),
                safety_settings=safety_settings,
                system_instruction=system_instruction,
                log_tag=log_tag,
            )

            budget_state = _reserve_qa_budget_call(model_name=model_name, log_tag=log_tag)
            started_at = time.time()
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config or None,
            )
            elapsed_ms = (time.time() - started_at) * 1000
            if not log_brief:
                budget_suffix = ""
                if budget_state:
                    budget_suffix = f" budget={budget_state.get('count')}/{budget_state.get('limit')}"
                logger.info(f"[Gemini] success key=...{masked_key} ({elapsed_ms:.0f}ms) model={model_name}{tag}{budget_suffix}")
            return response

        except Exception as exc:
            error_msg = str(exc)
            error_lower = error_msg.lower()
            is_timeout = any(token in error_lower for token in ["504", "deadline", "timeout", "timed out"])
            if is_timeout:
                logger.warning(
                    f"[Gemini] timeout{tag} key=...{masked_key} attempt={attempt + 1}/{max_attempts} :: {error_msg[:200]}"
                )
                time.sleep(1)
                continue
            if any(token in error_msg for token in ["429", "403", "Quota", "limit", "Resource has been exhausted"]):
                logger.warning(
                    f"[Gemini] quota{tag} key=...{masked_key} attempt={attempt + 1}/{max_attempts} :: {error_msg[:180]}"
                )
                quota_exceeded_keys.add(current_key)
                time.sleep(2 + attempt)
            else:
                logger.error(
                    f"[Gemini] error{tag} key=...{masked_key} attempt={attempt + 1}/{max_attempts} :: {error_msg[:250]}"
                )
                time.sleep(1)

    logger.error(f"[Gemini] fatal{tag}: attempts exhausted ({max_attempts})")
    return None
