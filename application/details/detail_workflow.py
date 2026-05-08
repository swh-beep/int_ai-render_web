import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from application.details.detail_analysis_stage import prepare_detail_generation_items
from application.details.detail_result_stage import build_detail_generation_output


def run_generate_details_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[str, str, str | None], str],
    persist_job_result: Callable[[dict, Optional[str]], None],
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    log_section: Callable[[str], None],
    detect_furniture_boxes: Callable[[str], list],
    canonical_category: Callable[[Optional[str]], str],
    build_item_target_key: Callable[..., str],
    max_concurrency_analysis: int,
    analyze_cropped_item: Callable[[str, dict], dict],
    attach_volume_ranks: Callable[[list], list],
    construct_dynamic_styles: Callable[[list], list],
    generate_detail_view: Callable[[str, dict, str, int, list | None], dict | str | None],
    normalize_label_for_match: Callable[[str], str],
    volume_ranking_snapshot: Callable[[list], list],
) -> dict:
    try:
        def _should_request_crop_extract(style: dict | None) -> bool:
            return str((style or {}).get("name") or "").startswith("Detail:")

        image_url = payload.get("image_url")
        moodboard_url = payload.get("moodboard_url")
        furniture_data = payload.get("furniture_data")
        audience = payload.get("audience")
        raw_absolute_deadline_ts = payload.get("absolute_deadline_ts")
        raw_minimum_budget_sec = payload.get("minimum_detail_budget_sec")
        try:
            absolute_deadline_ts = float(raw_absolute_deadline_ts) if raw_absolute_deadline_ts is not None else None
        except Exception:
            absolute_deadline_ts = None
        try:
            minimum_detail_budget_sec = max(1.0, float(raw_minimum_budget_sec or 5.0))
        except Exception:
            minimum_detail_budget_sec = 5.0
        budgeted_mode = absolute_deadline_ts is not None

        aud = normalize_audience(audience)
        prefix_detail_user = build_s3_prefix(aud, "detailrendered", "user-photos")
        prefix_detail_rendered = build_s3_prefix(aud, "detailrendered", "rendered")

        def _remaining_deadline_sec() -> float | None:
            if absolute_deadline_ts is None:
                return None
            try:
                return max(0.0, float(absolute_deadline_ts) - float(time.time()))
            except Exception:
                return 0.0

        def _build_best_effort_output(message: str, analyzed_items: list | None = None) -> dict:
            furniture_boxes = []
            for item in analyzed_items or []:
                if not isinstance(item, dict):
                    continue
                furniture_boxes.append(
                    {
                        "label": item.get("label"),
                        "target_key": item.get("target_key"),
                        "source_index": item.get("source_index"),
                        "category": item.get("category"),
                        "category_canonical": item.get("category_canonical"),
                        "box_2d": item.get("box_2d"),
                        "source_box_2d": item.get("source_box_2d"),
                        "box_source": item.get("box_source"),
                        "crop_path": item.get("crop_path"),
                        "volume_rank": item.get("volume_rank"),
                        "volume_proxy": item.get("volume_proxy"),
                        "volume_rank_basis": item.get("volume_rank_basis"),
                        "category_score": item.get("category_score"),
                    }
                )
            return {
                "details": [],
                "furniture_boxes": furniture_boxes,
                "used_cutout_references": [],
                "volume_ranking": volume_ranking_snapshot(analyzed_items or []),
                "message": message,
            }

        def _budgeted_style_cap(style_count: int, remaining_budget_sec: float | None) -> int:
            if remaining_budget_sec is None:
                return style_count
            try:
                budget = max(0.0, float(remaining_budget_sec))
            except Exception:
                budget = 0.0
            if budget < minimum_detail_budget_sec:
                return 0
            cap = int(budget // 18.0)
            return max(1, min(style_count, max(1, min(6, cap or 1))))

        def _ret(result: dict) -> dict:
            persist_job_result(result, audience=aud)
            return result

        if budgeted_mode:
            remaining_budget = _remaining_deadline_sec()
            if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                return _ret(_build_best_effort_output("Detail generation skipped due to deadline budget exhaustion"))

        local_path = materialize_input(image_url, "detail_src")
        if not local_path or not os.path.exists(local_path):
            return _ret({"error": "Original image not found"})
        resolve_image_url(local_path, prefix_detail_user)

        unique_id = uuid.uuid4().hex[:6]
        log_section(f"[Detail View] REQUEST START ({unique_id}) - Smart Analysis Mode")

        analyzed_items = prepare_detail_generation_items(
            furniture_data=furniture_data,
            moodboard_url=moodboard_url,
            local_path=local_path,
            materialize_input=materialize_input,
            detect_furniture_boxes=detect_furniture_boxes,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
            max_concurrency_analysis=max_concurrency_analysis,
            analyze_cropped_item=analyze_cropped_item,
            attach_volume_ranks=attach_volume_ranks,
            normalize_label_for_match=normalize_label_for_match,
        )
        if budgeted_mode:
            remaining_budget = _remaining_deadline_sec()
            if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                return _ret(
                    _build_best_effort_output(
                        "Detail generation skipped after main render because deadline budget is too low",
                        analyzed_items=analyzed_items,
                    )
                )

        dynamic_styles = construct_dynamic_styles(analyzed_items)
        if aud == "external":
            detail_only = []
            for style in dynamic_styles:
                name = style.get("name") or ""
                if name.startswith("Detail:"):
                    detail_only.append(style)
            dynamic_styles = detail_only[:9]
        if not dynamic_styles:
            if budgeted_mode:
                return _ret(_build_best_effort_output("No detail styles available within the remaining deadline budget", analyzed_items=analyzed_items))
            return _ret({"error": "No styles available"})

        generated_paths = []

        def _append_detail_result(index: int, style_payload: dict, result) -> None:
            if not result:
                return
            if isinstance(result, dict):
                generated_paths.append(
                    {
                        "index": index,
                        "path": result.get("path"),
                        "style_name": result.get("style_name") or style_payload.get("name"),
                        "style_ratio": result.get("aspect_ratio") or style_payload.get("ratio"),
                        "style_target_key": style_payload.get("target_key"),
                        "style_target_label": style_payload.get("target_label"),
                        "cutout_ref_count": int(result.get("cutout_ref_count") or 0),
                        "cutout_ref_labels": list(result.get("cutout_ref_labels") or []),
                    }
                )
                return
            generated_paths.append(
                {
                    "index": index,
                    "path": result,
                    "style_name": style_payload.get("name"),
                    "style_ratio": style_payload.get("ratio"),
                    "style_target_key": style_payload.get("target_key"),
                    "style_target_label": style_payload.get("target_label"),
                    "cutout_ref_count": 0,
                    "cutout_ref_labels": [],
                }
            )

        if budgeted_mode:
            dynamic_styles = dynamic_styles[: _budgeted_style_cap(len(dynamic_styles), _remaining_deadline_sec())]
            print(f"?? Generating {len(dynamic_styles)} Budgeted Dynamic Shots...", flush=True)
            if len(dynamic_styles) > 1:
                futures = []
                max_workers = min(6, len(dynamic_styles))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for index, style in enumerate(dynamic_styles):
                        remaining_budget = _remaining_deadline_sec()
                        if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                            break
                        style_payload = dict(style or {})
                        if remaining_budget is not None:
                            style_payload["timeout_sec"] = max(1.0, min(90.0, float(remaining_budget) - 1.0))
                        futures.append(
                            (
                                index,
                                style_payload,
                                executor.submit(
                                    generate_detail_view,
                                    local_path,
                                    style_payload,
                                    unique_id,
                                    index + 1,
                                    analyzed_items,
                                    prefer_crop_extract=_should_request_crop_extract(style_payload),
                                ),
                            )
                        )
                    for index, style_payload, future in futures:
                        _append_detail_result(index, style_payload, future.result())
            else:
                for index, style in enumerate(dynamic_styles):
                    remaining_budget = _remaining_deadline_sec()
                    if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                        break
                    style_payload = dict(style or {})
                    if remaining_budget is not None:
                        style_payload["timeout_sec"] = max(1.0, min(90.0, float(remaining_budget) - 1.0))
                    result = generate_detail_view(
                        local_path,
                        style_payload,
                        unique_id,
                        index + 1,
                        analyzed_items,
                        prefer_crop_extract=_should_request_crop_extract(style_payload),
                    )
                    _append_detail_result(index, style_payload, result)
        else:
            print(f"?? Generating {len(dynamic_styles)} Dynamic Shots...", flush=True)
            with ThreadPoolExecutor(max_workers=15) as executor:
                futures = []
                for index, style in enumerate(dynamic_styles):
                    futures.append(
                        (
                            index,
                            executor.submit(
                                generate_detail_view,
                                local_path,
                                style,
                                unique_id,
                                index + 1,
                                analyzed_items,
                                prefer_crop_extract=_should_request_crop_extract(style),
                            ),
                        )
                    )
                for index, future in futures:
                    result = future.result()
                    style_payload = dynamic_styles[index] if index < len(dynamic_styles) else {}
                    _append_detail_result(index, style_payload, result)

        print(f"=== [Detail View] complete: {len(generated_paths)} generated ===", flush=True)
        if not generated_paths:
            if budgeted_mode:
                return _ret(
                    _build_best_effort_output(
                        "Detail generation reached the deadline budget before any detail shot completed",
                        analyzed_items=analyzed_items,
                    )
                )
            return _ret({"error": "Failed to generate images"})

        output = build_detail_generation_output(
            analyzed_items=analyzed_items,
            generated_paths=generated_paths,
            materialize_input=materialize_input,
            resolve_image_url=resolve_image_url,
            prefix_detail_user=prefix_detail_user,
            prefix_detail_rendered=prefix_detail_rendered,
            normalize_label_for_match=normalize_label_for_match,
            volume_ranking_snapshot=volume_ranking_snapshot,
        )
        if not output.get("details"):
            if budgeted_mode:
                output = _build_best_effort_output(
                    "Detail generation completed without usable detail shots before the deadline",
                    analyzed_items=analyzed_items,
                )
                return _ret(output)
            return _ret({"error": "Failed to generate images"})

        return _ret(output)
    except Exception as exc:
        print(f"[Detail Error] {exc}", flush=True)
        aud = normalize_audience(payload.get("audience"))
        result = {"error": str(exc)}
        persist_job_result(result, audience=aud)
        return result
