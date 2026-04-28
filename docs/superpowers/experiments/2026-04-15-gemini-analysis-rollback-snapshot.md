# Gemini Analysis Rollback Snapshot

Date: 2026-04-15
Scope: analysis-only experiment baseline before GPT A/B

Current production defaults in main.py:
- MODEL_NAME = gemini-3.1-flash-image-preview
- ANALYSIS_MODEL_NAME = gemini-3.1-pro-preview
- DETECT_FURNITURE_MODEL_NAME = gemini-3.1-pro-preview
- ROOM_ONLY_MODEL_NAME = gemini-3.1-pro-preview
- RANK_MODEL_NAME = gemini-3.1-pro-preview
- REMAP_MODEL_NAME = gemini-3.1-pro-preview

Rollback instructions:
- Restore the six values above in main.py or matching env vars.
- Keep external /cart and /preset contracts unchanged.
