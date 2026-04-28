import gc
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


def _normalize_variant_result(result: Any) -> dict[str, Any]:
    def _coerce_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple) or isinstance(value, set):
            return list(value)
        return [value]

    if isinstance(result, dict):
        normalized = dict(result)
    else:
        normalized = {"path": result}

    path = normalized.get("path")
    if path is not None and not isinstance(path, str):
        path = str(path)

    normalized["path"] = path
    normalized["scalecheck_fail_count"] = int(normalized.get("scalecheck_fail_count") or 0)
    normalized["scalecheck_retry_count"] = int(normalized.get("scalecheck_retry_count") or 0)
    normalized["scale_check_failed"] = bool(normalized.get("scale_check_failed", False))
    normalized["scalecheck_issues"] = _coerce_list(normalized.get("scalecheck_issues"))
    normalized["scalecheck_failed_rules"] = _coerce_list(normalized.get("scalecheck_failed_rules"))
    return normalized


def _generate_one_variant(
    index: int,
    *,
    step1_img: str,
    style_prompt: str,
    ref_input,
    unique_id: str,
    furniture_specs_text: str | None,
    furniture_specs_json: dict | None,
    dimensions: str,
    placement: str,
    scale_guide_path: str | None,
    primary_item: dict | None,
    room_dims_parsed: dict,
    wall_span_norm,
    size_hierarchy: Any,
    scale_plan: dict | None = None,
    geometry_contract: dict | None = None,
    scene_contract: dict | None = None,
    placement_plan: dict | None = None,
    start_time: float,
    room_planes,
    windows_present: bool,
    room_analysis_text: str,
    enable_scale_check: bool,
    generate_furnished_room: Callable[..., str | dict[str, Any] | None],
):
    sub_id = f"{unique_id}_v{index+1}"
    try:
        result = generate_furnished_room(
            step1_img,
            style_prompt,
            ref_input,
            sub_id,
            furniture_specs=furniture_specs_text,
            furniture_specs_json=furniture_specs_json,
            room_dimensions=dimensions,
            placement_instructions=placement,
            scale_guide_path=scale_guide_path,
            primary_item=primary_item,
            room_dims_parsed=room_dims_parsed,
            wall_span_norm=wall_span_norm,
            size_hierarchy=size_hierarchy,
            scale_plan=scale_plan,
            geometry_contract=geometry_contract,
            scene_contract=scene_contract,
            placement_plan=placement_plan,
            start_time=start_time,
            room_planes=room_planes,
            windows_present=windows_present,
            room_analysis_text=room_analysis_text,
            enable_scale_check=enable_scale_check,
        )
        if result:
            return _normalize_variant_result(result)
    except Exception as exc:
        print(f"   ??[Variation {index+1}] ???: {exc}", flush=True)
    return None


def run_render_variant_stage(
    *,
    step1_img: str,
    style_prompt: str,
    ref_input,
    unique_id: str,
    furniture_specs_text: str | None,
    furniture_specs_json: dict | None,
    dimensions: str,
    placement: str,
    scale_guide_path: str | None,
    primary_item: dict | None,
    room_dims_parsed: dict,
    wall_span_norm,
    size_hierarchy: Any,
    scale_plan: dict | None = None,
    geometry_contract: dict | None = None,
    scene_contract: dict | None = None,
    placement_plan: dict | None = None,
    start_time: float,
    room_planes,
    windows_present: bool,
    room_analysis_text: str,
    enable_scale_check: bool,
    generate_furnished_room: Callable[..., str | dict[str, Any] | None],
    max_variants: int = 2,
    max_workers: int = 2,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    generated_results: list[dict[str, Any]] = []
    try:
        variant_count = max(0, int(max_variants or 0))
    except Exception:
        variant_count = 0
    try:
        worker_count = max(1, int(max_workers or 1))
    except Exception:
        worker_count = 1
    variant_indexes = [int(start_index) + index for index in range(variant_count)]
    if not variant_indexes:
        return generated_results
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _generate_one_variant,
                index,
                step1_img=step1_img,
                style_prompt=style_prompt,
                ref_input=ref_input,
                unique_id=unique_id,
                furniture_specs_text=furniture_specs_text,
                furniture_specs_json=furniture_specs_json,
                dimensions=dimensions,
                placement=placement,
                scale_guide_path=scale_guide_path,
                primary_item=primary_item,
                room_dims_parsed=room_dims_parsed,
                wall_span_norm=wall_span_norm,
                size_hierarchy=size_hierarchy,
                scale_plan=scale_plan,
                geometry_contract=geometry_contract,
                scene_contract=scene_contract,
                placement_plan=placement_plan,
                start_time=start_time,
                room_planes=room_planes,
                windows_present=windows_present,
                room_analysis_text=room_analysis_text,
                enable_scale_check=enable_scale_check,
                generate_furnished_room=generate_furnished_room,
            )
            for index in variant_indexes
        ]
        for future in futures:
            result = future.result()
            if result:
                generated_results.append(_normalize_variant_result(result))
            gc.collect()
    return generated_results
