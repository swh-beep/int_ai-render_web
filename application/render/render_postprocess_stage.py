import os
from dataclasses import dataclass
from typing import Callable


@dataclass
class RenderPostprocessStageResult:
    generated_results: list[str]
    full_analyzed_data: list[dict]
    volume_ranking: list[dict]


def _reorder_generated_results(
    generated_results: list[str],
    full_analyzed_data: list[dict],
    audience: str,
    *,
    rank_best_variant: Callable[[list, list], int | None],
) -> list[str]:
    reordered = list(generated_results or [])
    try:
        best_idx = rank_best_variant(reordered, full_analyzed_data)
        if best_idx is not None and 0 <= best_idx < len(reordered):
            if audience == "external":
                reordered = [reordered[best_idx]]
            else:
                best_path = reordered[best_idx]
                reordered = [best_path] + [path for idx, path in enumerate(reordered) if idx != best_idx]
    except Exception:
        pass
    if audience == "external" and len(reordered) > 1:
        reordered = reordered[:1]
    return reordered


def _refresh_main_render_boxes(
    generated_results: list[str],
    full_analyzed_data: list[dict],
    *,
    refresh_item_boxes_from_main_render: Callable[[str, list], list],
    log_brief: bool,
    logger,
    audience: str,
) -> list[dict]:
    refreshed = full_analyzed_data or []
    try:
        if refreshed and generated_results:
            main_render_path = generated_results[0]
            if main_render_path and os.path.exists(main_render_path):
                refreshed = refresh_item_boxes_from_main_render(main_render_path, refreshed)
                if not log_brief:
                    logger.info("[DetailBox] main-render remap applied (%s): %d items", audience, len(refreshed))
    except Exception as exc:
        logger.exception(f"[DetailBox] main-render remap failed: {exc}")
    return refreshed


def _attach_volume_metadata(
    full_analyzed_data: list[dict],
    *,
    attach_volume_ranks: Callable[[list], list],
    volume_ranking_snapshot: Callable[[list], list],
    logger,
) -> tuple[list[dict], list[dict]]:
    ranked = full_analyzed_data or []
    try:
        ranked = attach_volume_ranks(ranked)
    except Exception as exc:
        logger.exception(f"[VolumeRank] attach failed: {exc}")
        ranked = ranked or []
    return ranked, volume_ranking_snapshot(ranked)


def run_render_postprocess_stage(
    *,
    generated_results: list[str],
    full_analyzed_data: list[dict],
    audience: str,
    rank_best_variant: Callable[[list, list], int | None],
    refresh_item_boxes_from_main_render: Callable[[str, list], list],
    attach_volume_ranks: Callable[[list], list],
    volume_ranking_snapshot: Callable[[list], list],
    logger,
    log_brief: bool,
) -> RenderPostprocessStageResult:
    reordered_results = _reorder_generated_results(
        generated_results,
        full_analyzed_data,
        audience,
        rank_best_variant=rank_best_variant,
    )
    refreshed_items = _refresh_main_render_boxes(
        reordered_results,
        full_analyzed_data,
        refresh_item_boxes_from_main_render=refresh_item_boxes_from_main_render,
        log_brief=log_brief,
        logger=logger,
        audience=audience,
    )
    ranked_items, volume_ranking = _attach_volume_metadata(
        refreshed_items,
        attach_volume_ranks=attach_volume_ranks,
        volume_ranking_snapshot=volume_ranking_snapshot,
        logger=logger,
    )
    return RenderPostprocessStageResult(
        generated_results=reordered_results,
        full_analyzed_data=ranked_items,
        volume_ranking=volume_ranking,
    )
