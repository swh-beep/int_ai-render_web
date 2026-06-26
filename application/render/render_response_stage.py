import os
from typing import Callable


def _build_selected_item_review(furniture_data: list[dict], selected_variant_review: dict | None) -> list[dict]:
    diagnostics = ((selected_variant_review or {}).get("scalecheck_diagnostics") or {}) if isinstance(selected_variant_review, dict) else {}
    matched_items = diagnostics.get("matched_items") or {}
    unmatched_lookup = {
        str(row.get("target_key") or row.get("item_key") or row.get("label") or "")
        for row in (diagnostics.get("unmatched_items") or [])
        if isinstance(row, dict)
    }
    rows = []
    for item in furniture_data or []:
        if not isinstance(item, dict):
            continue
        target_key = str(item.get("target_key") or item.get("label") or "")
        if target_key in matched_items:
            status = "matched"
        elif target_key in unmatched_lookup:
            status = "unmatched"
        else:
            status = "unknown"
        rows.append(
            {
                "target_key": item.get("target_key"),
                "label": item.get("label"),
                "category": item.get("category"),
                "status": status,
            }
        )
    return rows


def _identity_review_from_variant(row: dict | None) -> dict | None:
    if not isinstance(row, dict):
        return None
    identity_summary = row.get("identity_qc_summary") if isinstance(row.get("identity_qc_summary"), dict) else {}
    identity_fail_count = int(row.get("identity_fail_count") if row.get("identity_fail_count") is not None else row.get("fidelity_fail_count") or 0)
    unmatched_source_count = int(row.get("unmatched_source_count") or 0)
    identity_issue_count = int(row.get("identity_issue_count") or (identity_fail_count + unmatched_source_count))
    identity_failed_rules = list(row.get("identity_failed_rules") or identity_summary.get("identity_failed_rules") or [])
    return {
        "variant_index": row.get("variant_index"),
        "path": row.get("path"),
        "pass": bool(identity_summary.get("pass")) and identity_issue_count == 0,
        "matched_source_count": int(row.get("matched_source_count") or 0),
        "unmatched_source_count": unmatched_source_count,
        "identity_fail_count": identity_fail_count,
        "identity_issue_count": identity_issue_count,
        "identity_failed_rules": identity_failed_rules,
        "qc_reason": row.get("qc_reason"),
    }


def _build_identity_qc_summary(
    selected_variant_review: dict | None,
    variant_diagnostics: list[dict] | None,
) -> dict:
    candidate_reviews = [
        review
        for review in (_identity_review_from_variant(row) for row in (variant_diagnostics or []))
        if isinstance(review, dict)
    ]
    failed_count = sum(1 for row in candidate_reviews if int(row.get("identity_issue_count") or 0) > 0)
    return {
        "selected": _identity_review_from_variant(selected_variant_review),
        "candidates": candidate_reviews,
        "candidate_count": len(candidate_reviews),
        "identity_failed_candidate_count": failed_count,
    }


def log_render_summary(
    summary: dict,
    *,
    log_summary: bool,
    logger,
) -> None:
    if not log_summary:
        return

    reasons = []
    if summary.get("dims_fail", 0):
        reasons.append(f"Dims fail={summary.get('dims_fail', 0)}")
    if summary.get("dims_warn", 0):
        reasons.append(f"Dims warn={summary.get('dims_warn', 0)}")
    if summary.get("scalecheck_fail", 0):
        reasons.append(f"ScaleCheck fail={summary.get('scalecheck_fail', 0)}")
    if summary.get("scale_guide_skipped", 0):
        reasons.append(f"Scale guide skipped={summary.get('scale_guide_skipped', 0)}")
    if reasons:
        logger.warning("WARNING: %s", "; ".join(reasons))


def build_render_response_payload(
    *,
    std_path: str,
    step1_img: str,
    scale_guide_path: str | None,
    generated_results: list[str],
    candidate_results: list[str] | None = None,
    selected_result_index: int | None = None,
    selected_result_reason: str | None = None,
    selected_variant_review: dict | None = None,
    variant_diagnostics: list[dict] | None = None,
    final_result_blocked: bool = False,
    scale_plan: dict | None = None,
    room_dims_contract: dict | None = None,
    geometry_contract: dict | None = None,
    scene_contract: dict | None = None,
    placement_plan: dict | None = None,
    include_replay_debug: bool = False,
    moodboard_url: str | None,
    furniture_data: list[dict],
    volume_ranking: list[dict],
    prefix_main_user: str,
    prefix_main_empty: str,
    prefix_main_rendered: str,
    prefix_main_candidates: str | None = None,
    artifact_root_prefix: str | None = None,
    resolve_image_url: Callable[[str | None, str | None], str | None],
) -> dict:
    final_before_url = resolve_image_url(step1_img, s3_prefix_override=prefix_main_empty)

    scale_guide_url = None
    try:
        if scale_guide_path and os.path.exists(scale_guide_path):
            scale_guide_url = resolve_image_url(scale_guide_path, s3_prefix_override=prefix_main_rendered)
    except Exception:
        pass

    candidate_paths = list(candidate_results or generated_results or [])
    candidate_prefix = prefix_main_candidates or prefix_main_rendered
    candidate_result_urls = [resolve_image_url(path, s3_prefix_override=candidate_prefix) for path in candidate_paths if path]
    delivery_paths = list(candidate_paths if final_result_blocked else (generated_results or []))
    result_urls = [
        resolve_image_url(path, s3_prefix_override=prefix_main_rendered)
        for path in delivery_paths
        if path
    ]
    if not result_urls and step1_img:
        result_urls = [resolve_image_url(step1_img, s3_prefix_override=prefix_main_empty)]

    selected_result_filename = None
    if delivery_paths:
        try:
            selected_result_filename = os.path.basename(delivery_paths[0])
        except Exception:
            selected_result_filename = None
    elif result_urls and step1_img:
        try:
            selected_result_filename = os.path.basename(step1_img)
        except Exception:
            selected_result_filename = None

    payload = {
        "original_url": resolve_image_url(std_path, s3_prefix_override=prefix_main_user),
        "empty_room_url": final_before_url,
        "result_url": result_urls[0] if result_urls else None,
        "result_urls": result_urls,
        "moodboard_url": moodboard_url,
        "scale_guide_url": scale_guide_url,
        "furniture_data": furniture_data,
        "volume_ranking": volume_ranking,
        "message": "QC blocked final selection" if final_result_blocked else "Complete",
    }
    identity_qc_summary = _build_identity_qc_summary(selected_variant_review, variant_diagnostics)
    if artifact_root_prefix:
        payload["artifact_manifest"] = {
            "root_prefix": artifact_root_prefix,
            "original_url": payload["original_url"],
            "empty_room_url": final_before_url,
            "candidate_result_urls": candidate_result_urls,
            "selected_result_urls": result_urls,
            "selected_result_index": selected_result_index,
            "selected_result_filename": selected_result_filename,
            "selected_result_reason": selected_result_reason,
            "final_result_blocked": bool(final_result_blocked),
            "identity_qc_summary": identity_qc_summary,
        }
    if include_replay_debug:
        payload["final_result_blocked"] = bool(final_result_blocked)
        payload["candidate_result_urls"] = candidate_result_urls
        payload["selected_result_index"] = selected_result_index
        payload["selected_result_filename"] = selected_result_filename
        payload["selected_result_reason"] = selected_result_reason
        payload["selected_variant_review"] = dict(selected_variant_review or {})
        payload["selected_item_review"] = _build_selected_item_review(furniture_data, selected_variant_review)
        payload["variant_diagnostics"] = list(variant_diagnostics or [])
        payload["scale_plan"] = dict(scale_plan or {})
        payload["room_dims_contract"] = dict(room_dims_contract or {})
        payload["geometry_contract"] = dict(geometry_contract or {})
        payload["scene_contract"] = dict(scene_contract or {})
        payload["placement_plan"] = dict(placement_plan or {})
    return payload
