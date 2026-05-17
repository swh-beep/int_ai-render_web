from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


DurationSec = Literal[3, 4, 5, 6, 7, 8, 9, 10]
AttemptStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
GroupStatus = Literal["DRAFT", "GENERATING", "REVIEWING", "COMPILING", "COMPLETED", "FAILED"]
GenerationMode = Literal["START_ONLY", "START_END", "NEXT_START_AS_END"]
ClipGenerationType = Literal["INITIAL", "REGENERATE", "PARTIAL"]


class MarketingReelClipCreate(BaseModel):
    client_image_id: str = Field(..., min_length=1)
    source_image_url: str = ""
    end_image_url: str | None = None
    generation_mode: GenerationMode = "START_ONLY"
    order: int = Field(..., ge=1)
    prompt: str = ""
    duration_sec: DurationSec = 5

    @model_validator(mode="after")
    def normalize_start_only_end_frame(self):
        if self.generation_mode == "START_ONLY":
            self.end_image_url = None
        return self


class MarketingReelGroupCreate(BaseModel):
    global_prompt: str = ""
    platform: str = ""
    tone: str = ""
    goal: str = ""
    clips: list[MarketingReelClipCreate] = Field(..., min_length=1, max_length=10)

    @model_validator(mode="after")
    def require_sequential_clip_order(self):
        expected_orders = list(range(1, len(self.clips) + 1))
        actual_orders = [clip.order for clip in self.clips]
        if sorted(actual_orders) != expected_orders:
            raise ValueError("Clip order must be a unique 1..N sequence")
        return self


class MarketingClipAttemptPayload(BaseModel):
    attempt_id: str = Field(..., min_length=1)
    clip_id: str = Field(..., min_length=1)
    clip_generation_id: str | None = None
    source_job_id: str = Field(..., min_length=1)
    source_job_item_index: int = Field(..., ge=0)
    prompt: str = ""
    duration_sec: DurationSec = 5
    status: AttemptStatus
    source_video_url: str | None = None
    download_url: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def completed_attempt_requires_video_url(self):
        if self.status == "COMPLETED" and not (self.source_video_url or "").strip():
            raise ValueError("COMPLETED attempts require source_video_url")
        return self


class MarketingClipAttemptUpdate(BaseModel):
    status: AttemptStatus | None = None
    source_video_url: str | None = None
    download_url: str | None = None
    error: str | None = None


class MarketingClipSourceImageUpdate(BaseModel):
    clip_id: str = Field(..., min_length=1)
    source_image_url: str = Field(..., min_length=1)
    end_image_url: str | None = None
    generation_mode: GenerationMode | None = None

    @model_validator(mode="after")
    def normalize_frame_consistency(self):
        mode: GenerationMode = self.generation_mode or ("START_END" if self.end_image_url else "START_ONLY")
        if mode in {"START_END", "NEXT_START_AS_END"} and not self.end_image_url:
            raise ValueError(f"{mode} clips require end_image_url")
        if mode == "START_ONLY":
            self.end_image_url = None
        self.generation_mode = mode
        return self


class MarketingClipSourceImagesUpdate(BaseModel):
    clips: list[MarketingClipSourceImageUpdate] = Field(..., min_length=1)


class MarketingClipGenerationCreate(BaseModel):
    generation_type: ClipGenerationType = "INITIAL"
    clip_ids: list[str] = Field(..., min_length=1)
    source_job_id: str | None = None


class MarketingClipApprovalPayload(BaseModel):
    attempt_id: str = Field(..., min_length=1)


class MarketingFinalResultPayload(BaseModel):
    compile_job_id: str = Field(..., min_length=1)
    final_video_url: str = Field(..., min_length=1)
    final_download_url: str | None = None
    final_title: str | None = None
    selected_attempt_ids: list[str] | None = None
    compile_payload_summary: Any = None


class MarketingGroupTitleUpdate(BaseModel):
    final_title: str = Field(..., min_length=1, max_length=255)


class MarketingGlobalPromptCreate(BaseModel):
    global_prompt: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def normalize_prompt(self):
        self.global_prompt = self.global_prompt.strip()
        if not self.global_prompt:
            raise ValueError("global_prompt is required")
        return self


class MarketingClipPromptCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    prompt: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def normalize_prompt(self):
        self.title = self.title.strip()
        self.prompt = self.prompt.strip()
        if not self.title:
            raise ValueError("title is required")
        if not self.prompt:
            raise ValueError("prompt is required")
        return self
