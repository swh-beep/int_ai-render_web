from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RenderScaleStageResult:
    room_dims_parsed: dict
    room_dims_valid: bool
    enable_scale_guidance: bool
    room_planes: Any = None
    wall_span_norm: tuple[float, float] = (0.0, 1.0)
    windows_present: bool | None = None
    room_analysis_text: str = ""
    furniture_specs_text: str | None = None
    furniture_specs_json: dict | None = None
    primary_item: dict | None = None
    scale_guide_path: str | None = None
    size_hierarchy: Any = None
    full_analyzed_data: list[dict] = field(default_factory=list)


def run_render_scale_stage(
    *,
    audience: str,
    dimensions: str,
    parse_room_dimensions_mm: Callable[[str], dict],
    room_dims_valid_fn: Callable[[dict], bool],
    logger,
) -> RenderScaleStageResult:
    room_dims_parsed = parse_room_dimensions_mm(dimensions or "")
    room_dims_valid = room_dims_valid_fn(room_dims_parsed)
    enable_scale_guidance = (audience == "internal") and room_dims_valid

    if audience == "external":
        try:
            logger.info("[Scale] external request -> guide/check disabled")
        except Exception:
            pass
    elif not room_dims_valid:
        try:
            logger.info("[Scale] internal request without valid room dimensions -> guide skipped")
        except Exception:
            pass

    return RenderScaleStageResult(
        room_dims_parsed=room_dims_parsed,
        room_dims_valid=room_dims_valid,
        enable_scale_guidance=enable_scale_guidance,
    )
