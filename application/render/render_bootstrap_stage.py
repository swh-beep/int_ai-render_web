from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class RenderBootstrapStageResult:
    unique_id: str
    start_time: float
    summary: dict[str, int]
    summary_token: Any = None


def _build_summary() -> dict[str, int]:
    return {
        "dims_fail": 0,
        "dims_warn": 0,
        "scalecheck_fail": 0,
        "scale_guide_skipped": 0,
    }


def run_render_bootstrap_stage(
    *,
    generate_unique_id: Callable[[], str],
    time_now: Callable[[], float],
    log_section: Callable[[str], None],
    summary_ref,
    request_label: str = "Integrated Analysis Mode",
) -> RenderBootstrapStageResult:
    unique_id = generate_unique_id()
    log_section(f"REQUEST START [{unique_id}] ({request_label})")
    start_time = time_now()
    summary = _build_summary()
    summary_token = summary_ref.set(summary)
    return RenderBootstrapStageResult(
        unique_id=unique_id,
        start_time=start_time,
        summary=summary,
        summary_token=summary_token,
    )
