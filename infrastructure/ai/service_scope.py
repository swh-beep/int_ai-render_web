from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


INTERNAL_SCOPE = "internal_tool"
EXTERNAL_SCOPES = (
    "kr_ai_designer",
    "kr_ai_consultant",
    "global_ai_designer",
    "global_ai_consultant",
)
PROVIDER_SCOPES = (INTERNAL_SCOPE, *EXTERNAL_SCOPES)

_SCOPE_ENV_SUFFIXES = {
    INTERNAL_SCOPE: "INTERNAL_TOOL",
    "kr_ai_designer": "KR_AI_DESIGNER",
    "kr_ai_consultant": "KR_AI_CONSULTANT",
    "global_ai_designer": "GLOBAL_AI_DESIGNER",
    "global_ai_consultant": "GLOBAL_AI_CONSULTANT",
}
_CLIENT_KEY_ENVS = {
    "kr_ai_designer": "KR_AI_DESIGNER_RENDER_CLIENT_KEYS",
    "kr_ai_consultant": "KR_AI_CONSULTANT_RENDER_CLIENT_KEYS",
    "global_ai_designer": "GLOBAL_AI_DESIGNER_RENDER_CLIENT_KEYS",
    "global_ai_consultant": "GLOBAL_AI_CONSULTANT_RENDER_CLIENT_KEYS",
}

_current_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar("ai_service_scope", default=None)


@dataclass(frozen=True)
class ScopedProviderKeys:
    openai_by_scope: dict[str, str]
    gemini_by_scope: dict[str, list[str]]


def parse_key_list(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.replace(";", ",").split(",") if part.strip()}


def current_ai_service_scope() -> str | None:
    return _current_scope.get()


@contextmanager
def ai_service_scope(scope: str | None) -> Iterator[None]:
    token = _current_scope.set(normalize_scope(scope))
    try:
        yield
    finally:
        _current_scope.reset(token)


def normalize_scope(scope: str | None) -> str | None:
    normalized = str(scope or "").strip().lower()
    if not normalized:
        return None
    return normalized


def attach_ai_service_scope(payload: dict, scope: str) -> dict:
    if not isinstance(payload, dict):
        return payload
    scoped = dict(payload)
    scoped["ai_service_scope"] = scope
    render_payload = scoped.get("render")
    if isinstance(render_payload, dict):
        scoped["render"] = {**render_payload, "ai_service_scope": scope}
    variants = scoped.get("variants")
    if isinstance(variants, list):
        scoped["variants"] = [
            _attach_variant_scope(variant, scope) if isinstance(variant, dict) else variant
            for variant in variants
        ]
    return scoped


def _attach_variant_scope(variant: dict, scope: str) -> dict:
    scoped_variant = dict(variant)
    scoped_variant["ai_service_scope"] = scope
    render_payload = scoped_variant.get("render")
    if isinstance(render_payload, dict):
        scoped_variant["render"] = {**render_payload, "ai_service_scope": scope}
    return scoped_variant


def scope_from_payload(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    scope = normalize_scope(payload.get("ai_service_scope"))
    if scope:
        return scope
    render_payload = payload.get("render")
    if isinstance(render_payload, dict):
        scope = normalize_scope(render_payload.get("ai_service_scope"))
        if scope:
            return scope
    return None


def load_external_scope_key_map(env: dict[str, str] | None = None) -> dict[str, set[str]]:
    env = env or os.environ
    return {scope: parse_key_list(env.get(var_name)) for scope, var_name in _CLIENT_KEY_ENVS.items()}


def validate_external_scope_keys(scope_key_map: dict[str, set[str]]) -> None:
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []
    for scope, keys in scope_key_map.items():
        for key in keys:
            previous = seen.get(key)
            if previous and previous != scope:
                duplicates.append((key, previous, scope))
            else:
                seen[key] = scope
    if duplicates:
        scopes = ", ".join(f"{left}/{right}" for _key, left, right in duplicates)
        raise RuntimeError(f"Renderer client key configured in multiple AI service scopes: {scopes}")


def resolve_external_scope(api_key: str | None, scope_key_map: dict[str, set[str]]) -> str | None:
    if not api_key:
        return None
    matches = [scope for scope, keys in scope_key_map.items() if api_key in keys]
    if len(matches) > 1:
        raise RuntimeError("Renderer client key configured in multiple AI service scopes")
    if matches:
        return matches[0]
    return None


def load_scoped_provider_keys(env: dict[str, str] | None = None) -> ScopedProviderKeys:
    env = env or os.environ
    openai_by_scope: dict[str, str] = {}
    gemini_by_scope: dict[str, list[str]] = {}
    for scope in PROVIDER_SCOPES:
        suffix = _SCOPE_ENV_SUFFIXES[scope]
        openai_key = str(env.get(f"OPENAI_API_KEY_{suffix}") or "").strip()
        gemini_key = str(env.get(f"GEMINI_API_KEY_{suffix}") or "").strip()
        if openai_key:
            openai_by_scope[scope] = openai_key
        gemini_by_scope[scope] = [gemini_key] if gemini_key else []
    return ScopedProviderKeys(
        openai_by_scope=openai_by_scope,
        gemini_by_scope=gemini_by_scope,
    )


def provider_key_source(scope: str | None, provider: str, keys: ScopedProviderKeys, logger: logging.Logger) -> tuple[str, str | None]:
    normalized = normalize_scope(scope) or current_ai_service_scope()
    if not normalized:
        raise RuntimeError(f"ai_service_scope is required for {provider}")
    if normalized not in PROVIDER_SCOPES:
        raise RuntimeError(f"{provider} key is not configured for ai_service_scope={normalized}")
    if provider == "openai":
        key = keys.openai_by_scope.get(normalized)
        if key:
            return key, "scoped"
    elif provider == "gemini":
        pool = keys.gemini_by_scope.get(normalized) or []
        if pool:
            return pool[0], "scoped"
    raise RuntimeError(f"{provider} key is not configured for ai_service_scope={normalized}")


def gemini_pool_for_scope(scope: str | None, keys: ScopedProviderKeys, logger: logging.Logger) -> tuple[list[str], str]:
    normalized = normalize_scope(scope) or current_ai_service_scope()
    if not normalized:
        raise RuntimeError("ai_service_scope is required for gemini")
    if normalized in PROVIDER_SCOPES and keys.gemini_by_scope.get(normalized):
        return list(keys.gemini_by_scope[normalized]), "scoped"
    key, source = provider_key_source(normalized, "gemini", keys, logger)
    return [key], source
