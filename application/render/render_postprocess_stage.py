import os
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class RenderPostprocessStageResult:
    generated_results: list[str]
    full_analyzed_data: list[dict]
    volume_ranking: list[dict]


def _bounded_timeout_for_deadline(
    requested_sec: float,
    *,
    absolute_deadline_ts: float | None,
    minimum_sec: float,
) -> int | None:
    if absolute_deadline_ts is None:
        return int(requested_sec)
    try:
        remaining = max(0.0, float(absolute_deadline_ts) - float(time.time()))
    except Exception:
        remaining = 0.0
    timeout_sec = min(float(requested_sec), remaining)
    if timeout_sec <= minimum_sec:
        return None
    return int(max(float(minimum_sec), timeout_sec))


def _reorder_generated_results(
    generated_results: list[str],
    rankable_results: list[str] | None,
    full_analyzed_data: list[dict],
    audience: str,
    allow_failed_rerank: bool = True,
    absolute_deadline_ts: float | None = None,
    *,
    rank_best_variant: Callable[..., int | None],
) -> list[str]:
    reordered = list(generated_results or [])
    if not allow_failed_rerank:
        return reordered
    ranking_timeout_sec = _bounded_timeout_for_deadline(
        18.0,
        absolute_deadline_ts=absolute_deadline_ts,
        minimum_sec=8.0,
    )
    if ranking_timeout_sec is None:
        return reordered
    candidates = [path for path in (rankable_results or []) if path in reordered]
    candidates = candidates or list(reordered)
    try:
        try:
            best_idx = rank_best_variant(
                candidates,
                full_analyzed_data,
                timeout_sec=ranking_timeout_sec,
                max_attempts=1,
            )
        except TypeError:
            best_idx = rank_best_variant(candidates, full_analyzed_data)
        if best_idx is not None and 0 <= best_idx < len(candidates):
            best_path = candidates[best_idx]
            if audience == "external":
                reordered = [best_path]
            else:
                reordered = [best_path] + [path for path in reordered if path != best_path]
    except Exception:
        pass
    return reordered


def _refresh_main_render_boxes(
    generated_results: list[str],
    full_analyzed_data: list[dict],
    *,
    refresh_item_boxes_from_main_render: Callable[[str, list], list],
    skip_main_render_remap: bool,
    log_brief: bool,
    logger,
    audience: str,
    absolute_deadline_ts: float | None = None,
) -> list[dict]:
    refreshed = full_analyzed_data or []
    if skip_main_render_remap:
        return refreshed
    remap_timeout_sec = _bounded_timeout_for_deadline(
        12.0,
        absolute_deadline_ts=absolute_deadline_ts,
        minimum_sec=6.0,
    )
    if remap_timeout_sec is None:
        return refreshed
    try:
        if refreshed and generated_results:
            main_render_path = generated_results[0]
            if main_render_path and os.path.exists(main_render_path):
                try:
                    refreshed = refresh_item_boxes_from_main_render(
                        main_render_path,
                        refreshed,
                        remap_detect_timeout_sec=remap_timeout_sec,
                        remap_detect_retry=0,
                        remap_detect_max_attempts=1,
                    )
                except TypeError:
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
    rankable_results: list[str] | None = None,
    full_analyzed_data: list[dict],
    audience: str,
    allow_failed_rerank: bool = True,
    rank_best_variant: Callable[[list, list], int | None],
    refresh_item_boxes_from_main_render: Callable[[str, list], list],
    attach_volume_ranks: Callable[[list], list],
    volume_ranking_snapshot: Callable[[list], list],
    logger,
    log_brief: bool,
    skip_main_render_remap: bool = False,
    absolute_deadline_ts: float | None = None,
) -> RenderPostprocessStageResult:
    reordered_results = _reorder_generated_results(
        generated_results,
        rankable_results,
        full_analyzed_data,
        audience,
        allow_failed_rerank=allow_failed_rerank,
        absolute_deadline_ts=absolute_deadline_ts,
        rank_best_variant=rank_best_variant,
    )
    refreshed_items = _refresh_main_render_boxes(
        reordered_results,
        full_analyzed_data,
        refresh_item_boxes_from_main_render=refresh_item_boxes_from_main_render,
        skip_main_render_remap=skip_main_render_remap,
        log_brief=log_brief,
        logger=logger,
        audience=audience,
        absolute_deadline_ts=absolute_deadline_ts,
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
