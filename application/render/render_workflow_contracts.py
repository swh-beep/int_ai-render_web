from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class RenderWorkflowRequest:
    file: Any
    room: str
    style: str
    variant: str
    moodboard: Any = None
    dimensions: str = ""
    placement: str = ""
    audience: str = ""
    moodboard_items: list[dict[str, Any]] | None = None


@dataclass
class RenderWorkflowRuntime:
    style_map: dict[str, str]
    generate_unique_id: Callable[[], str]
    time_now: Callable[[], float]
    log_section: Callable[[str], None]
    summary_ref: Any
    reset_summary_token: Callable[[Any], None]
    logger: Any
    log_brief: bool
    log_summary: bool
    use_s3_moodboard: bool
    max_concurrency_analysis: int
    cart_max_analysis_workers: int


@dataclass
class RenderWorkflowStorageServices:
    normalize_audience: Callable[[str | None], str]
    build_s3_prefix: Callable[[str | None, str | None, str | None], str]
    standardize_image: Callable[..., str]
    materialize_input: Callable[[str | None, str], str | None]
    resolve_image_url: Callable[[str | None, str | None], str | None]
    find_s3_moodboard_key: Callable[[str, str, str], str | None]
    s3_public_url: Callable[[str], str]


@dataclass
class RenderWorkflowAnalysisServices:
    parse_room_dimensions_mm: Callable[[str], dict]
    room_dims_valid_fn: Callable[[dict], bool]
    build_item_target_key: Callable[..., str]
    canonical_category: Callable[[str | None], str]
    detect_furniture_boxes: Callable[[str], list]
    analyze_room_structure: Callable[..., dict]
    analyze_cropped_item: Callable[..., dict]
    normalize_dims_dict: Callable[[dict], dict]
    parse_object_dimensions_mm: Callable[[str], dict]
    build_furniture_specs_json: Callable[[list], dict]
    create_scale_guide_overlay_with_model: Callable[..., str | None]
    match_aspect_to_target: Callable[[str, str], str | None]


@dataclass
class RenderWorkflowGenerationServices:
    generate_empty_room: Callable[..., tuple[str, str | None]]
    generate_furnished_room: Callable[..., str | None]


@dataclass
class RenderWorkflowPostprocessServices:
    rank_best_variant: Callable[[list, list], int | None]
    refresh_item_boxes_from_main_render: Callable[[str, list], list]
    attach_volume_ranks: Callable[[list], list]
    volume_ranking_snapshot: Callable[[list], list]


@dataclass
class RenderWorkflowDependencies:
    runtime: RenderWorkflowRuntime
    storage: RenderWorkflowStorageServices
    analysis: RenderWorkflowAnalysisServices
    generation: RenderWorkflowGenerationServices
    postprocess: RenderWorkflowPostprocessServices
