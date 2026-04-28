from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class UpscaleRequest(BaseModel):
    image_url: str


class FinalizeRequest(BaseModel):
    image_url: str


class InternalRenderRequest(BaseModel):
    image_url: str
    room: str
    style: str
    variant: str
    moodboard_url: Optional[str] = None
    dimensions: Optional[str] = ""
    placement: Optional[str] = ""


class PresetRenderRequest(BaseModel):
    image_url: str
    preset_id: Optional[str] = None
    room: Optional[str] = None
    style: Optional[str] = None
    variant: Optional[str] = None
    dimensions: Optional[str] = ""
    placement: Optional[str] = ""


class CartItem(BaseModel):
    id: str
    category: str
    image_url: str
    qty: int = 1
    dims_mm: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    options: Optional[Any] = None


class CartRenderRequest(BaseModel):
    image_url: str
    items: List[CartItem]
    room: Optional[str] = None
    style: Optional[str] = None
    variant: Optional[str] = None
    dimensions: Optional[str] = ""
    placement: Optional[str] = ""


class ExternalRenderVideoRequest(BaseModel):
    render_job_id: str
    clip_count: int = 4
    cfg_scale: float = 0.5


class DetailRequest(BaseModel):
    image_url: str
    moodboard_url: Optional[str] = None
    furniture_data: Optional[List[Dict[str, Any]]] = None
    audience: Optional[str] = None


class RegenerateDetailRequest(BaseModel):
    original_image_url: str
    style_index: int = 1
    target_key: Optional[str] = None
    target_label: Optional[str] = None
    style_index_mode: Optional[str] = "auto"
    moodboard_url: Optional[str] = None
    furniture_data: Optional[List[Dict[str, Any]]] = None
    audience: Optional[str] = None


class VideoClip(BaseModel):
    url: str
    motion: str = "static"
    effect: str = "none"
    speed: float = 1.0


class VideoCreateRequest(BaseModel):
    clips: List[VideoClip]
    duration: str = "5"
    cfg_scale: float = 0.85
    mode: Optional[str] = None
    target_total_sec: Optional[float] = None
    include_intro_outro: Optional[bool] = None
    intro_url: Optional[str] = None
    outro_url: Optional[str] = None


class SourceItem(BaseModel):
    url: str
    motion: str = "static"
    effect: str = "none"
    custom_motion_prompt: Optional[str] = None
    custom_effect_prompt: Optional[str] = None


class SourceGenRequest(BaseModel):
    items: List[SourceItem]
    cfg_scale: float = 0.5


class CompileClip(BaseModel):
    video_url: str
    speed: float = 1.0
    trim_start: float = 0.0
    trim_end: float = 5.0


class CompileRequest(BaseModel):
    clips: List[CompileClip]
    include_intro_outro: bool = False
    intro_url: Optional[str] = None
    outro_url: Optional[str] = None
