COMPACT_ITEM_ANALYSIS_PROFILE = "compact"
DETAILED_ITEM_ANALYSIS_PROFILE = "detailed"


def normalize_item_analysis_profile(value, *, default: str = DETAILED_ITEM_ANALYSIS_PROFILE) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"compact", "fast", "minimal"}:
        return COMPACT_ITEM_ANALYSIS_PROFILE
    if normalized in {"detailed", "detail", "full", "long", "ultra", "ultra_detailed"}:
        return DETAILED_ITEM_ANALYSIS_PROFILE
    return DETAILED_ITEM_ANALYSIS_PROFILE if default != COMPACT_ITEM_ANALYSIS_PROFILE else COMPACT_ITEM_ANALYSIS_PROFILE
