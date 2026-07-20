import json
from typing import Optional

from fastapi import HTTPException, Request

from infrastructure.ai.service_scope import (
    INTERNAL_SCOPE,
    LEGACY_SCOPE,
    resolve_external_scope,
)


def extract_api_key(request: Request) -> Optional[str]:
    key = request.headers.get("x-api-key") or request.headers.get("X-API-KEY")
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    return key or None


def resolve_api_role(
    api_key: Optional[str],
    internal_api_keys: set[str],
    external_api_keys: set[str],
) -> Optional[str]:
    if not api_key:
        return None
    if api_key in internal_api_keys:
        return "internal"
    if api_key in external_api_keys:
        return "external"
    return None


def resolve_api_scope(
    api_key: Optional[str],
    internal_api_keys: set[str],
    external_api_keys: set[str],
    external_scope_keys: dict[str, set[str]] | None = None,
) -> Optional[str]:
    if not api_key:
        return None
    if api_key in internal_api_keys:
        return INTERNAL_SCOPE
    external_scope = resolve_external_scope(api_key, external_scope_keys or {}, external_api_keys)
    if external_scope:
        return external_scope
    return None


def require_role(
    request: Request,
    allowed_roles: set[str],
    api_auth_disabled: bool,
    internal_api_keys: set[str],
    external_api_keys: set[str],
) -> str:
    if api_auth_disabled or not (internal_api_keys or external_api_keys):
        return "internal"
    role = resolve_api_role(extract_api_key(request), internal_api_keys, external_api_keys)
    if not role:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    if role == "internal":
        if "internal" in allowed_roles:
            return role
        raise HTTPException(status_code=403, detail="Forbidden")
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Forbidden")
    return role


def require_ai_service_scope(
    request: Request,
    allowed_roles: set[str],
    api_auth_disabled: bool,
    internal_api_keys: set[str],
    external_api_keys: set[str],
    external_scope_keys: dict[str, set[str]] | None = None,
) -> str:
    if api_auth_disabled or not (internal_api_keys or external_api_keys or any((external_scope_keys or {}).values())):
        return INTERNAL_SCOPE
    api_key = extract_api_key(request)
    scope = resolve_api_scope(api_key, internal_api_keys, external_api_keys, external_scope_keys)
    if not scope:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    role = "external" if scope in (set((external_scope_keys or {}).keys()) | {LEGACY_SCOPE}) else "internal"
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Forbidden")
    return scope


def apply_cart_limits(items: list[dict], cart_max_items: int) -> tuple[list[dict], list[dict]]:
    src = list(items or [])
    if len(src) <= cart_max_items:
        return src, []

    kept = src[:cart_max_items]
    dropped = []
    for idx, it in enumerate(src[cart_max_items:], start=cart_max_items + 1):
        row = dict(it or {})
        row["drop_reason"] = "max_items_exceeded"
        row["drop_index"] = idx
        dropped.append(row)
    return kept, dropped


def build_cart_summary(items: list[dict]) -> str:
    lines = []
    for it in items:
        dims = it.get("dims_mm") or {}
        w = dims.get("w") or dims.get("width") or dims.get("width_mm")
        d = dims.get("d") or dims.get("depth") or dims.get("depth_mm")
        h = dims.get("h") or dims.get("height") or dims.get("height_mm")
        options = it.get("options")
        opt_text = ""
        try:
            if isinstance(options, dict) and options:
                opt_text = " options=" + json.dumps(options, ensure_ascii=False)
            elif isinstance(options, list) and options:
                opt_text = " options=" + json.dumps(options, ensure_ascii=False)
            elif isinstance(options, str) and options.strip():
                opt_text = " options=" + options.strip()
        except Exception:
            opt_text = ""
        lines.append(f"- {it.get('category')} x{it.get('qty')} (W={w} D={d} H={h} mm) id={it.get('id')}{opt_text}")
    return "Customer-selected items:\n" + "\n".join(lines)
