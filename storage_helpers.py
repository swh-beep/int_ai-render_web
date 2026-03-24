import json
import mimetypes
import os
import re
from typing import Callable, Optional
from urllib.parse import urlparse


def s3_prefix_from_url(url: str, s3_bucket: str) -> Optional[str]:
    try:
        if not url:
            return None
        parsed = urlparse(url)
        if not parsed.scheme.startswith("http"):
            return None
        if s3_bucket and s3_bucket not in parsed.netloc:
            return None
        path = parsed.path.lstrip("/")
        if not path or "/" not in path:
            return None
        return path.rsplit("/", 1)[0] + "/"
    except Exception:
        return None


def s3_enabled(s3_bucket: str, aws_region: str) -> bool:
    return bool(s3_bucket and aws_region)


def normalize_s3_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix.strip("/").rstrip("/") + "/"


def s3_public_url(s3_bucket: str, aws_region: str, key: str) -> str:
    return f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/{key}"


def job_result_prefix(
    s3_prefix: str,
    job_result_s3_prefix: str,
    audience: Optional[str],
    build_s3_prefix: Callable[[Optional[str], Optional[str], Optional[str]], str],
) -> str:
    base = normalize_s3_prefix(s3_prefix)
    if job_result_s3_prefix:
        return f"{base}{normalize_s3_prefix(job_result_s3_prefix)}"
    if audience:
        return build_s3_prefix(audience, "job-results")
    return f"{base}job-results/"


def job_result_key(
    job_id: str,
    s3_prefix: str,
    job_result_s3_prefix: str,
    audience: Optional[str],
    build_s3_prefix: Callable[[Optional[str], Optional[str], Optional[str]], str],
) -> Optional[str]:
    if not job_id:
        return None
    prefix = job_result_prefix(s3_prefix, job_result_s3_prefix, audience, build_s3_prefix)
    return f"{prefix}{job_id}.json"


def job_result_key_candidates(
    job_id: str,
    s3_prefix: str,
    job_result_s3_prefix: str,
    build_s3_prefix: Callable[[Optional[str], Optional[str], Optional[str]], str],
) -> list[str]:
    if not job_id:
        return []
    base = normalize_s3_prefix(s3_prefix)
    candidates: list[str] = []
    if job_result_s3_prefix:
        candidates.append(f"{base}{normalize_s3_prefix(job_result_s3_prefix)}{job_id}.json")
    else:
        candidates.append(f"{build_s3_prefix('external', 'job-results')}{job_id}.json")
        candidates.append(f"{build_s3_prefix('internal', 'job-results')}{job_id}.json")
        candidates.append(f"{base}job-results/{job_id}.json")
    seen = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def save_job_result_s3(
    job_id: str,
    result: dict,
    audience: Optional[str],
    s3_bucket: str,
    aws_region: str,
    s3_prefix: str,
    job_result_s3_prefix: str,
    build_s3_prefix: Callable[[Optional[str], Optional[str], Optional[str]], str],
    get_s3_client: Callable[[], object],
) -> Optional[str]:
    if not s3_enabled(s3_bucket, aws_region):
        return None
    key = job_result_key(job_id, s3_prefix, job_result_s3_prefix, audience, build_s3_prefix)
    if not key:
        return None
    try:
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        get_s3_client().put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        return s3_public_url(s3_bucket, aws_region, key)
    except Exception:
        return None


def load_job_result_s3(
    job_id: str,
    s3_bucket: str,
    aws_region: str,
    s3_prefix: str,
    job_result_s3_prefix: str,
    build_s3_prefix: Callable[[Optional[str], Optional[str], Optional[str]], str],
    get_s3_client: Callable[[], object],
) -> Optional[dict]:
    if not s3_enabled(s3_bucket, aws_region):
        return None
    for key in job_result_key_candidates(job_id, s3_prefix, job_result_s3_prefix, build_s3_prefix):
        try:
            obj = get_s3_client().get_object(Bucket=s3_bucket, Key=key)
            data = obj["Body"].read()
            return json.loads(data.decode("utf-8"))
        except Exception:
            continue
    return None


def s3_list_keys(
    prefix: str,
    s3_bucket: str,
    aws_region: str,
    get_s3_client: Callable[[], object],
    max_keys: int = 1000,
) -> list[str]:
    if not s3_enabled(s3_bucket, aws_region):
        return []
    try:
        resp = get_s3_client().list_objects_v2(Bucket=s3_bucket, Prefix=prefix, MaxKeys=max_keys)
        contents = resp.get("Contents") or []
        return [obj.get("Key") for obj in contents if obj.get("Key")]
    except Exception:
        return []


def find_s3_moodboard_key(
    safe_room: str,
    safe_style: str,
    variant: str,
    build_s3_prefix: Callable[[Optional[str], Optional[str], Optional[str]], str],
    s3_list_keys_fn: Callable[[str, int], list[str]],
) -> Optional[str]:
    prefix_root = normalize_s3_prefix(build_s3_prefix(None, "moodboard"))
    if not prefix_root:
        prefix_root = ""
    pattern = rf"(?:^|[^0-9]){re.escape(str(variant))}(?:[^0-9]|$)"

    nested_prefix = f"{prefix_root}{safe_room}/{safe_style}/"
    keys = s3_list_keys_fn(nested_prefix, 1000)
    if keys:
        for key in keys:
            base = os.path.basename(key)
            if re.search(pattern, base, re.IGNORECASE):
                return key
        return keys[0]

    name_prefix = f"{safe_room}_{safe_style}_"
    search_prefix = f"{prefix_root}{name_prefix}"
    keys = s3_list_keys_fn(search_prefix, 1000)
    if not keys:
        keys = s3_list_keys_fn(prefix_root, 1000)
    if not keys:
        return None
    candidates = [key for key in keys if os.path.basename(key).startswith(name_prefix)]
    if not candidates:
        candidates = keys
    for key in candidates:
        base = os.path.basename(key)
        if re.search(pattern, base, re.IGNORECASE):
            return key
    return candidates[0] if candidates else None


def publish_image(
    local_path: Optional[str],
    s3_prefix_override: Optional[str],
    s3_prefix: str,
    s3_bucket: str,
    aws_region: str,
    published_url_cache: dict,
    get_s3_client: Callable[[], object],
) -> Optional[str]:
    if not local_path:
        return None
    if local_path.startswith("http://") or local_path.startswith("https://"):
        return local_path
    key_prefix = normalize_s3_prefix(s3_prefix_override if s3_prefix_override is not None else s3_prefix)
    cache_key = f"{local_path}::{key_prefix}"
    if cache_key in published_url_cache:
        return published_url_cache[cache_key]
    if not s3_enabled(s3_bucket, aws_region):
        return None
    if not os.path.exists(local_path):
        return None
    key = f"{key_prefix}{os.path.basename(local_path)}"
    content_type, _ = mimetypes.guess_type(local_path)
    extra = {"ContentType": content_type} if content_type else None
    if extra:
        get_s3_client().upload_file(local_path, s3_bucket, key, ExtraArgs=extra)
    else:
        get_s3_client().upload_file(local_path, s3_bucket, key)
    url = s3_public_url(s3_bucket, aws_region, key)
    published_url_cache[cache_key] = url
    return url


def resolve_image_url(
    local_path: Optional[str],
    s3_prefix_override: Optional[str],
    s3_prefix: str,
    s3_bucket: str,
    aws_region: str,
    s3_required: bool,
    published_url_cache: dict,
    get_s3_client: Callable[[], object],
) -> Optional[str]:
    if not local_path:
        return None
    if local_path.startswith("http://") or local_path.startswith("https://"):
        return local_path
    if local_path.startswith("/outputs/"):
        if s3_required:
            raise RuntimeError("S3_REQUIRED is enabled but output is still local (/outputs).")
        return local_path
    if local_path.startswith("/assets/"):
        return local_path
    url = publish_image(
        local_path,
        s3_prefix_override,
        s3_prefix,
        s3_bucket,
        aws_region,
        published_url_cache,
        get_s3_client,
    )
    if url:
        return url
    if s3_required:
        raise RuntimeError("S3_REQUIRED is enabled but S3 is not configured or upload failed.")
    if os.path.exists(local_path):
        return f"/outputs/{os.path.basename(local_path)}"
    return None


def is_allowed_download_url(
    url: str,
    request_host: str,
    s3_bucket: str,
    allowed_hosts: set[str] | None = None,
    allow_public_cloud_hosts: bool = False,
) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.netloc or "").lower()
        req_host = (request_host or "").lower()
        extra_hosts = {h.lower() for h in (allowed_hosts or set()) if h}
        if host == req_host and host:
            return True
        if host in extra_hosts:
            return True
        if any(host.endswith(f".{allowed}") for allowed in extra_hosts):
            return True
        if s3_bucket and s3_bucket.lower() in host:
            return True
        if allow_public_cloud_hosts and host.endswith("cloudfront.net"):
            return True
        if allow_public_cloud_hosts and host.endswith("amazonaws.com"):
            return True
    except Exception:
        return False
    return False
