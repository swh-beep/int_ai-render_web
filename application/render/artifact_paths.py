from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable


def _safe_segment(value: Any) -> str:
    raw = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")


def artifact_date_path(created_at: Any = None) -> str:
    dt = None
    if isinstance(created_at, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(created_at), tz=timezone.utc)
        except Exception:
            dt = None
    elif isinstance(created_at, str) and created_at.strip():
        raw = created_at.strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
        except Exception:
            dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y/%m/%d")


def build_job_artifact_root(
    build_s3_prefix: Callable[[str | None, str | None, str | None], str],
    audience: str | None,
    job_id: str | None,
    created_at: Any = None,
    *,
    category: str = "mainrendered",
) -> str:
    safe_job_id = _safe_segment(job_id)
    if not safe_job_id:
        return ""
    base = str(build_s3_prefix(audience, category, None) or "").strip("/")
    date_path = artifact_date_path(created_at)
    return "/".join(part for part in [base, date_path, safe_job_id] if part) + "/"


def artifact_subprefix(root_prefix: str | None, subfolder: str) -> str:
    root = str(root_prefix or "").strip("/")
    safe_subfolder = str(subfolder or "").strip("/")
    if not root or not safe_subfolder:
        return root + ("/" if root else "")
    return f"{root}/{safe_subfolder.strip('/')}/"
