import base64
import io
import mimetypes
import os
import time
from types import SimpleNamespace
from typing import Any, Sequence

import requests
from PIL import Image


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_OPENAI_REASONING_FALLBACK = {
    "xhigh": "high",
}


def _normalize_image(image: Image.Image, *, max_edge: int = 1024) -> Image.Image:
    normalized = image.convert("RGB")
    current_max = max(normalized.size)
    if current_max > max_edge:
        scale = max_edge / current_max
        normalized = normalized.resize(
            (max(1, int(normalized.size[0] * scale)), max(1, int(normalized.size[1] * scale))),
            Image.Resampling.LANCZOS,
        )
    return normalized


def _image_to_data_url(image: Image.Image, *, default_format: str = "JPEG") -> str:
    normalized = _normalize_image(image)
    fmt = (default_format or "JPEG").upper()
    mime = Image.MIME.get(fmt) or mimetypes.types_map.get(f".{fmt.lower()}") or "image/jpeg"
    buffer = io.BytesIO()
    save_kwargs = {"format": fmt}
    if fmt == "JPEG":
        save_kwargs["quality"] = 85
        save_kwargs["optimize"] = True
    normalized.save(buffer, **save_kwargs)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _build_openai_input(contents: Sequence[Any]) -> list[dict[str, Any]]:
    user_content: list[dict[str, Any]] = []
    for item in contents or []:
        if isinstance(item, str):
            user_content.append({"type": "input_text", "text": item})
            continue
        if isinstance(item, Image.Image):
            user_content.append({"type": "input_image", "image_url": _image_to_data_url(item)})
            continue
        user_content.append({"type": "input_text", "text": str(item)})
    return [{"role": "user", "content": user_content}]


def _extract_output_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text") or "").strip()
    if direct:
        return direct

    texts: list[str] = []
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            ctype = str(content.get("type") or "").strip().lower()
            if ctype not in {"output_text", "text"}:
                continue
            text_value = content.get("text")
            if isinstance(text_value, dict):
                text_value = text_value.get("value")
            text = str(text_value or "").strip()
            if text:
                texts.append(text)
    return "\n".join(texts).strip()


def _normalize_reasoning_effort(reasoning_effort: str | None) -> str:
    normalized = str(reasoning_effort or "").strip().lower()
    return normalized or "low"


def _should_fallback_reasoning(exc: Exception, current_effort: str) -> bool:
    fallback = _OPENAI_REASONING_FALLBACK.get(current_effort)
    if not fallback:
        return False
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    try:
        response_text = str(getattr(response, "text", "") or "")
    except Exception:
        response_text = ""
    message = f"{exc} {response_text}".lower()
    if status_code not in {400, 422}:
        return False
    return any(token in message for token in ("reasoning", "effort", current_effort, "unsupported", "invalid"))


def call_openai_analysis(
    model_name: str,
    contents: Sequence[Any],
    request_options: dict | None,
    *,
    api_key: str,
    logger: Any,
    log_brief: bool,
    system_instruction: str | None = None,
    log_tag: str | None = None,
    reasoning_effort: str = "xhigh",
    base_url: str = OPENAI_RESPONSES_URL,
    session: requests.sessions.Session | None = None,
):
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when ANALYSIS_PROVIDER=openai")

    request_options = dict(request_options or {})
    timeout_sec = max(10, int(request_options.get("timeout") or 120))
    timeout_cap_raw = str(os.getenv("OPENAI_ANALYSIS_TIMEOUT_CAP_SEC", "") or "").strip()
    if timeout_cap_raw:
        try:
            timeout_cap = max(10, int(timeout_cap_raw))
            timeout_sec = min(timeout_sec, timeout_cap)
        except Exception:
            pass
    try:
        max_attempts = max(1, int(request_options.get("max_attempts") or os.getenv("OPENAI_ANALYSIS_MAX_ATTEMPTS", "3")))
    except Exception:
        max_attempts = 3
    tag = f" tag={log_tag}" if log_tag else ""
    current_reasoning_effort = _normalize_reasoning_effort(reasoning_effort)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    request_sender = session.post if session is not None else requests.post
    last_exc: Exception | None = None
    attempt = 0
    while attempt < max_attempts:
        started_at = time.time()
        try:
            payload: dict[str, Any] = {
                "model": model_name,
                "input": _build_openai_input(contents),
            }
            if system_instruction:
                payload["instructions"] = system_instruction
            if model_name.startswith("gpt-5"):
                payload["reasoning"] = {"effort": current_reasoning_effort}
            response = request_sender(
                base_url,
                headers=headers,
                json=payload,
                timeout=timeout_sec,
            )
            elapsed_ms = (time.time() - started_at) * 1000
            response.raise_for_status()
            data = response.json()
            text = _extract_output_text(data)
            if not log_brief:
                logger.info(f"[OpenAI] success ({elapsed_ms:.0f}ms) model={model_name}{tag}")
            return SimpleNamespace(text=text, raw=data)
        except Exception as exc:
            last_exc = exc
            elapsed_ms = (time.time() - started_at) * 1000
            if _should_fallback_reasoning(exc, current_reasoning_effort):
                fallback_effort = _OPENAI_REASONING_FALLBACK[current_reasoning_effort]
                logger.warning(
                    f"[OpenAI] reasoning fallback ({elapsed_ms:.0f}ms) model={model_name}{tag} {current_reasoning_effort}->{fallback_effort} :: {exc}"
                )
                current_reasoning_effort = fallback_effort
                continue
            attempt += 1
            logger.error(
                f"[OpenAI] error ({elapsed_ms:.0f}ms) model={model_name}{tag} attempt={attempt}/{max_attempts} :: {exc}"
            )
            if attempt < max_attempts:
                time.sleep(1 + max(0, attempt - 1))

    raise RuntimeError(f"OpenAI analysis call failed after {max_attempts} attempts: {last_exc}")
