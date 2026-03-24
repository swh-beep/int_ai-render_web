import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class RenderInputStageResult:
    timestamp: int
    safe_name: str
    raw_path: str
    std_path: str


def _sanitize_filename(filename: str | None) -> str:
    safe_name = "".join([char for char in (filename or "upload") if char.isalnum() or char in "._-"])
    return safe_name or "upload"


def run_render_input_stage(
    *,
    upload_file: Any,
    unique_id: str,
    time_now: Callable[[], float],
    standardize_image: Callable[..., str],
    output_dir: str = "outputs",
) -> RenderInputStageResult:
    timestamp = int(time_now())
    safe_name = _sanitize_filename(getattr(upload_file, "filename", None))
    raw_path = os.path.join(output_dir, f"raw_{timestamp}_{unique_id}_{safe_name}")
    with open(raw_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

    std_path = standardize_image(raw_path)
    return RenderInputStageResult(
        timestamp=timestamp,
        safe_name=safe_name,
        raw_path=raw_path,
        std_path=std_path,
    )
