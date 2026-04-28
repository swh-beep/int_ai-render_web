import base64
import io
import mimetypes
import time
from urllib.parse import urlparse
from types import SimpleNamespace
from typing import Any, Sequence

import requests
from PIL import Image


OPENAI_IMAGE_GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
OPENAI_IMAGE_EDITS_URL = "https://api.openai.com/v1/images/edits"


def _image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _ordered_prompt_and_images(
    contents: Sequence[Any],
    *,
    system_instruction: str | None = None,
) -> tuple[str, list[tuple[str, bytes, str]]]:
    prompt_parts: list[str] = []
    images: list[tuple[str, bytes, str]] = []
    image_index = 0

    if system_instruction:
        prompt_parts.append(system_instruction.strip())

    for item in contents or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                prompt_parts.append(text)
            continue

        if isinstance(item, Image.Image):
            image_index += 1
            filename = f"image_{image_index}.png"
            image_bytes = _image_to_png_bytes(item)
            images.append((filename, image_bytes, "image/png"))
            prompt_parts.append(f"[Image {image_index}]")
            continue

        text = str(item).strip()
        if text:
            prompt_parts.append(text)

    return "\n\n".join(part for part in prompt_parts if part).strip(), images


def _extract_image_bytes(payload: dict[str, Any], *, timeout_sec: int = 60) -> list[bytes]:
    output: list[bytes] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        b64_json = item.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            try:
                output.append(base64.b64decode(b64_json))
            except Exception:
                continue
            continue
        image_url = item.get("url")
        if isinstance(image_url, str) and image_url.strip():
            parsed = urlparse(image_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            try:
                response = requests.get(image_url, timeout=timeout_sec)
                if int(getattr(response, "status_code", 0) or 0) >= 400:
                    continue
                content = bytes(getattr(response, "content", b"") or b"")
                if content:
                    output.append(content)
            except Exception:
                continue
    return output


def _build_gemini_like_response(image_blobs: list[bytes]) -> SimpleNamespace:
    parts = [
        SimpleNamespace(
            inline_data=SimpleNamespace(data=image_bytes),
        )
        for image_bytes in image_blobs
    ]
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts))
    return SimpleNamespace(
        candidates=[candidate] if parts else [],
        parts=parts,
        text="",
    )


def _extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = str(error_payload.get("message") or "").strip()
            if message:
                return message

    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text
    return f"HTTP {response.status_code}"


def _is_verification_gate_error(response: requests.Response, model_name: str) -> bool:
    normalized_model = str(model_name or "").strip().lower()
    if normalized_model != "gpt-image-2" or int(getattr(response, "status_code", 0) or 0) != 403:
        return False
    return "must be verified to use the model" in _extract_error_message(response).lower()


def _is_gpt_image_model(model_name: str) -> bool:
    return str(model_name or "").strip().lower().startswith("gpt-image-")


def _should_retry(exc: Exception) -> bool:
    message = str(exc).lower()
    retry_tokens = ("timeout", "timed out", "429", "500", "502", "503", "504", "temporar")
    return any(token in message for token in retry_tokens)


def call_openai_image(
    model_name: str,
    contents: Sequence[Any],
    request_options: dict | None,
    _safety_settings: dict | None,
    *,
    api_key: str,
    logger: Any,
    log_brief: bool,
    system_instruction: str | None = None,
    log_tag: str | None = None,
    fallback_model_name: str | None = None,
):
    if not api_key:
        if logger is not None:
            logger.error("[OpenAIImage] missing API key")
        return None

    prompt, images = _ordered_prompt_and_images(contents, system_instruction=system_instruction)
    timeout_sec = max(10, int((request_options or {}).get("timeout") or 180))
    size = str((request_options or {}).get("size") or "1536x1024").strip() or "1536x1024"
    quality = str((request_options or {}).get("quality") or "high").strip() or "high"
    output_format = str((request_options or {}).get("output_format") or "png").strip() or "png"
    tag = f" tag={log_tag}" if log_tag else ""
    try:
        max_attempts = max(1, int((request_options or {}).get("max_attempts") or 3))
    except Exception:
        max_attempts = 3

    headers = {"Authorization": f"Bearer {api_key}"}

    active_model_name = str(model_name or "").strip() or "gpt-image-2"
    fallback_model_name = str(fallback_model_name or "").strip()
    verification_fallback_used = False

    for attempt in range(1, max_attempts + 1):
        while True:
            started_at = time.time()
            try:
                if images:
                    data = {
                        "model": active_model_name,
                        "prompt": prompt,
                        "size": size,
                        "quality": quality,
                        "output_format": output_format,
                    }
                    if not _is_gpt_image_model(active_model_name):
                        data["response_format"] = "b64_json"
                    files = []
                    for filename, image_bytes, mime_type in images:
                        files.append(("image[]", (filename, image_bytes, mime_type)))
                    response = requests.post(
                        OPENAI_IMAGE_EDITS_URL,
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=timeout_sec,
                    )
                else:
                    response = requests.post(
                        OPENAI_IMAGE_GENERATIONS_URL,
                        headers={**headers, "Content-Type": "application/json"},
                    json={
                        "model": active_model_name,
                        "prompt": prompt,
                        "size": size,
                        "quality": quality,
                        "output_format": output_format,
                        **({"response_format": "b64_json"} if not _is_gpt_image_model(active_model_name) else {}),
                    },
                    timeout=timeout_sec,
                )

                elapsed_ms = (time.time() - started_at) * 1000
                if int(getattr(response, "status_code", 0) or 0) >= 400:
                    if (
                        not verification_fallback_used
                        and fallback_model_name
                        and fallback_model_name != active_model_name
                        and _is_verification_gate_error(response, active_model_name)
                    ):
                        verification_fallback_used = True
                        if logger is not None:
                            log_warning = getattr(logger, "warning", None) or getattr(logger, "info", None)
                            if callable(log_warning):
                                log_warning(
                                    f"[OpenAIImage] verification gate for model={active_model_name}; retrying with fallback_model={fallback_model_name}{tag}"
                                )
                        active_model_name = fallback_model_name
                        continue
                    raise RuntimeError(f"HTTP {response.status_code}: {_extract_error_message(response)}")
                payload = response.json()
                image_blobs = _extract_image_bytes(payload, timeout_sec=timeout_sec)
                if not image_blobs:
                    raise RuntimeError("OpenAI image response missing b64_json data")
                if not log_brief and logger is not None:
                    mode = "edit" if images else "generate"
                    logger.info(f"[OpenAIImage] success ({elapsed_ms:.0f}ms) model={active_model_name} mode={mode}{tag}")
                response_obj = _build_gemini_like_response(image_blobs)
                response_obj.model_name = active_model_name
                return response_obj
            except Exception as exc:
                elapsed_ms = (time.time() - started_at) * 1000
                if logger is not None:
                    logger.error(
                        f"[OpenAIImage] error ({elapsed_ms:.0f}ms) model={active_model_name} attempt={attempt}/{max_attempts}{tag} :: {exc}"
                    )
                if attempt >= max_attempts or not _should_retry(exc):
                    return None
                time.sleep(min(2 * attempt, 5))
                break

    return None
