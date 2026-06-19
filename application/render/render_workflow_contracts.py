from dataclasses import dataclass
from typing import Any, Callable


def _noop_explicit_room_dims_contract(*args, **kwargs):
    return None


def _noop_estimate_room_dims_contract(*args, **kwargs):
    return None


def _noop_scene_contract(*args, **kwargs):
    return {}


def _noop_product_identity_bundle(items, *args, **kwargs):
    rows = list(items or [])
    return rows, [dict((row or {}).get("product_identity") or {}) for row in rows if isinstance(row, dict)]


def _noop_placement_plan(*, analyzed_items=None, **kwargs):
    rows = list(analyzed_items or [])
    return {}, rows


def _noop_geometry_contract(*args, **kwargs):
    return {}


def _noop_archetype_strategies(items, *args, **kwargs):
    rows = list(items or [])
    return rows, [dict((row or {}).get("archetype_strategy") or {}) for row in rows if isinstance(row, dict)]


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
    simple_generation_mode: bool = False


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
    total_timeout_limit_sec: float


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
    build_explicit_room_dims_contract: Callable[..., Any] = _noop_explicit_room_dims_contract
    estimate_room_dims_contract: Callable[..., Any] = _noop_estimate_room_dims_contract
    build_scene_contract: Callable[..., Any] = _noop_scene_contract
    build_product_identity_bundle: Callable[..., Any] = _noop_product_identity_bundle
    build_placement_plan: Callable[..., Any] = _noop_placement_plan
    build_geometry_contract: Callable[..., Any] = _noop_geometry_contract
    build_archetype_strategies: Callable[..., Any] = _noop_archetype_strategies


@dataclass
class RenderWorkflowGenerationServices:
    generate_empty_room: Callable[..., tuple[str, str | None]]
    generate_furnished_room: Callable[..., str | dict[str, Any] | None]


@dataclass
class RenderWorkflowPostprocessServices:
    rank_best_variant: Callable[..., int | None]
    refresh_item_boxes_from_main_render: Callable[..., list]
    attach_volume_ranks: Callable[[list], list]
    volume_ranking_snapshot: Callable[[list], list]


@dataclass
class RenderWorkflowDependencies:
    runtime: RenderWorkflowRuntime
    storage: RenderWorkflowStorageServices
    analysis: RenderWorkflowAnalysisServices
    generation: RenderWorkflowGenerationServices
    postprocess: RenderWorkflowPostprocessServices
