import json
import os
import random
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import google.generativeai as genai


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
    max_attempts = 5
    tag = f" tag={log_tag}" if log_tag else ""

    try:
        content_types = []
        for item in contents or []:
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
            genai.configure(api_key=current_key)
            model = (
                genai.GenerativeModel(model_name, system_instruction=system_instruction)
                if system_instruction
                else genai.GenerativeModel(model_name)
            )

            budget_state = _reserve_qa_budget_call(model_name=model_name, log_tag=log_tag)
            started_at = time.time()
            response = model.generate_content(
                contents,
                request_options=request_options,
                safety_settings=safety_settings,
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

    logger.error(f"[Gemini] fatal{tag}: all keys failed")
    return None
