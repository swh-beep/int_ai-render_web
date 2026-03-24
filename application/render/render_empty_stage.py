from dataclasses import dataclass
from typing import Callable


@dataclass
class RenderEmptyStageResult:
    step1_img: str
    step1_raw: str | None


def run_render_empty_stage(
    *,
    std_path: str,
    unique_id: str,
    start_time: float,
    generate_empty_room: Callable[..., tuple[str, str | None]],
) -> RenderEmptyStageResult:
    step1_img, step1_raw = generate_empty_room(
        std_path,
        unique_id,
        start_time,
        stage_name="Stage 1: Intermediate Clean",
        return_raw=True,
    )
    if not step1_raw:
        step1_raw = step1_img
    return RenderEmptyStageResult(step1_img=step1_img, step1_raw=step1_raw)
