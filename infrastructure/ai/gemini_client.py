import random
import time
from collections.abc import Sequence
from typing import Any

import google.generativeai as genai


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

            started_at = time.time()
            response = model.generate_content(
                contents,
                request_options=request_options,
                safety_settings=safety_settings,
            )
            elapsed_ms = (time.time() - started_at) * 1000
            if not log_brief:
                logger.info(f"[Gemini] success key=...{masked_key} ({elapsed_ms:.0f}ms) model={model_name}{tag}")
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
