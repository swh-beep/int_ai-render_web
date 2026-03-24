import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from application.details.detail_analysis_stage import load_analyzed_items
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
        image_url = payload.get("image_url")
        moodboard_url = payload.get("moodboard_url")
        furniture_data = payload.get("furniture_data")
        audience = payload.get("audience")

        aud = normalize_audience(audience)
        prefix_detail_user = build_s3_prefix(aud, "detailrendered", "user-photos")
        prefix_detail_rendered = build_s3_prefix(aud, "detailrendered", "rendered")

        def _ret(result: dict) -> dict:
            persist_job_result(result, audience=aud)
            return result

        local_path = materialize_input(image_url, "detail_src")
        if not local_path or not os.path.exists(local_path):
            return _ret({"error": "Original image not found"})
        resolve_image_url(local_path, prefix_detail_user)

        unique_id = uuid.uuid4().hex[:6]
        log_section(f"[Detail View] REQUEST START ({unique_id}) - Smart Analysis Mode")

        analyzed_items = load_analyzed_items(
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
            return _ret({"error": "No styles available"})

        generated_paths = []
        print(f"?? Generating {len(dynamic_styles)} Dynamic Shots...", flush=True)
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = []
            for index, style in enumerate(dynamic_styles):
                futures.append((index, executor.submit(generate_detail_view, local_path, style, unique_id, index + 1, analyzed_items)))
            for index, future in futures:
                result = future.result()
                if not result:
                    continue
                if isinstance(result, dict):
                    generated_paths.append(
                        {
                            "index": index,
                            "path": result.get("path"),
                            "style_name": result.get("style_name") or (dynamic_styles[index].get("name") if index < len(dynamic_styles) else None),
                            "style_target_key": (dynamic_styles[index].get("target_key") if index < len(dynamic_styles) else None),
                            "style_target_label": (dynamic_styles[index].get("target_label") if index < len(dynamic_styles) else None),
                            "cutout_ref_count": int(result.get("cutout_ref_count") or 0),
                            "cutout_ref_labels": list(result.get("cutout_ref_labels") or []),
                        }
                    )
                else:
                    generated_paths.append(
                        {
                            "index": index,
                            "path": result,
                            "style_name": (dynamic_styles[index].get("name") if index < len(dynamic_styles) else None),
                            "style_target_key": (dynamic_styles[index].get("target_key") if index < len(dynamic_styles) else None),
                            "style_target_label": (dynamic_styles[index].get("target_label") if index < len(dynamic_styles) else None),
                            "cutout_ref_count": 0,
                            "cutout_ref_labels": [],
                        }
                    )

        print(f"=== [Detail View] complete: {len(generated_paths)} generated ===", flush=True)
        if not generated_paths:
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
            return _ret({"error": "Failed to generate images"})

        return _ret(output)
    except Exception as exc:
        print(f"[Detail Error] {exc}", flush=True)
        aud = normalize_audience(payload.get("audience"))
        result = {"error": str(exc)}
        persist_job_result(result, audience=aud)
        return result
