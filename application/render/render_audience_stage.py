from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class RenderAudienceStageResult:
    audience: str
    enable_scale_check: bool
    prefix_main_user: str
    prefix_main_empty: str
    prefix_main_rendered: str
    prefix_customize: str


def run_render_audience_stage(
    *,
    audience: Optional[str],
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[Optional[str], Optional[str], Optional[str]], str],
) -> RenderAudienceStageResult:
    aud = normalize_audience(audience)
    return RenderAudienceStageResult(
        audience=aud,
        enable_scale_check=True,
        prefix_main_user=build_s3_prefix(aud, "mainrendered", "user-photos"),
        prefix_main_empty=build_s3_prefix(aud, "mainrendered", "empty"),
        prefix_main_rendered=build_s3_prefix(aud, "mainrendered", "rendered"),
        prefix_customize=build_s3_prefix(aud, "customize"),
    )
