from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_render_job_timeout_defaults_cover_slow_image_workflows() -> None:
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert 'TOTAL_TIMEOUT_LIMIT = max(60, int(os.getenv("TOTAL_TIMEOUT_LIMIT", "1800")))' in main_source
    assert 'RQ_JOB_TIMEOUT = int(os.getenv("RQ_JOB_TIMEOUT", "1800"))' in main_source


def test_render_workflow_fallback_timeout_matches_queue_default() -> None:
    render_workflow_source = (ROOT / "application" / "render" / "render_workflow.py").read_text(encoding="utf-8")
    room_workflow_source = (ROOT / "application" / "render" / "render_room_workflow.py").read_text(encoding="utf-8")

    assert "total_timeout_limit_sec: float = 1800.0" in render_workflow_source
    assert "total_timeout_limit_sec or 1800.0" in render_workflow_source
    assert "total_timeout_limit_sec or 1800.0" in room_workflow_source


def test_default_kling_concurrency_allows_four_parallel_source_clips() -> None:
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert 'VIDEO_MAX_CONCURRENCY = int(os.getenv("VIDEO_MAX_CONCURRENCY", "4"))' in main_source


def test_worker_loggers_do_not_duplicate_rq_messages_through_root() -> None:
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert 'logging.getLogger("rq.worker").propagate = False' in main_source
