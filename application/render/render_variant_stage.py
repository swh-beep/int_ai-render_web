import gc
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


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
    start_time: float,
    room_planes,
    windows_present: bool,
    room_analysis_text: str,
    enable_scale_check: bool,
    generate_furnished_room: Callable[..., str | None],
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
            start_time=start_time,
            room_planes=room_planes,
            windows_present=windows_present,
            room_analysis_text=room_analysis_text,
            enable_scale_check=enable_scale_check,
        )
        if result:
            return result
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
    start_time: float,
    room_planes,
    windows_present: bool,
    room_analysis_text: str,
    enable_scale_check: bool,
    generate_furnished_room: Callable[..., str | None],
    max_variants: int = 3,
    max_workers: int = 3,
) -> list[str]:
    generated_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
                start_time=start_time,
                room_planes=room_planes,
                windows_present=windows_present,
                room_analysis_text=room_analysis_text,
                enable_scale_check=enable_scale_check,
                generate_furnished_room=generate_furnished_room,
            )
            for index in range(max_variants)
        ]
        for future in futures:
            result = future.result()
            if result:
                generated_results.append(result)
            gc.collect()
    return generated_results
